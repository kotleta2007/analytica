from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select

from app.collector.backfill import (
    DayOutcome,
    backfill,
    cron_times_from_user_raw,
    daily_days_from_history,
)
from app.collector.day_close import DayConfig
from app.collector.snapshots import collect
from app.db.engine import session_scope
from app.db.models import DailyRecord, TaskEvent, TaskRow
from app.habitica.types import HistoryEntry, task_from_json
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data

USER = "00000000-0000-4000-8000-000000000001"
SUMMER = DayConfig(day_start=11, timezone_offset=-120)


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def done(when: datetime) -> HistoryEntry:
    return HistoryEntry(at=when, value=1.0, is_due=True, completed=True)


def miss(when: datetime) -> HistoryEntry:
    return HistoryEntry(at=when, value=0.0, is_due=True, completed=False)


CRONS = [at(2, 9, 30), at(3, 9, 30), at(4, 12, 0), at(5, 9, 30)]


def test_completed_entries_land_on_their_own_day() -> None:
    days = daily_days_from_history([done(at(1, 20)), done(at(2, 7))], SUMMER, CRONS)

    # 02 07:00Z is before dayStart (09:00Z) so it is still Habitica day 09-01
    assert days == {date(2026, 9, 1): DayOutcome(was_due=True, completed=True)}


def test_a_cron_miss_is_about_the_day_that_ended() -> None:
    days = daily_days_from_history([miss(at(2, 9, 30))], SUMMER, CRONS)

    assert days == {date(2026, 9, 1): DayOutcome(was_due=True, completed=False)}


def test_a_late_cron_still_describes_the_previous_day() -> None:
    days = daily_days_from_history([miss(at(4, 12, 0))], SUMMER, CRONS)

    assert days == {date(2026, 9, 3): DayOutcome(was_due=True, completed=False)}


def test_a_cron_miss_wins_over_a_tick_synced_at_the_wrong_moment() -> None:
    # ticked "at cron time" on the 2nd (an offline tick from the 1st, synced late),
    # then cron on the 3rd took damage for the 2nd
    days = daily_days_from_history([done(at(2, 9, 30)), miss(at(3, 9, 30))], SUMMER, CRONS)

    assert days == {date(2026, 9, 2): DayOutcome(was_due=True, completed=False)}


def test_an_untick_between_crons_is_ignored() -> None:
    history = [done(at(2, 14)), miss(at(2, 15)), done(at(2, 16))]

    days = daily_days_from_history(history, SUMMER, CRONS)

    assert days == {date(2026, 9, 2): DayOutcome(was_due=True, completed=True)}


def test_an_untick_that_stays_is_settled_by_the_next_cron() -> None:
    history = [done(at(2, 14)), miss(at(2, 15)), miss(at(3, 9, 30))]

    days = daily_days_from_history(history, SUMMER, CRONS)

    assert days == {date(2026, 9, 2): DayOutcome(was_due=True, completed=False)}


def test_cron_times_parse_both_formats() -> None:
    raw = {
        "history": {
            "exp": [
                {"date": 1757581200000, "value": 1},
                {"date": "2026-09-11T10:07:31.619Z", "value": 2},
            ]
        }
    }

    times = cron_times_from_user_raw(raw)

    assert times == [
        datetime(2025, 9, 11, 9, 0, tzinfo=UTC),
        datetime(2026, 9, 11, 10, 7, 31, 619000, tzinfo=UTC),
    ]


def fixture_client() -> FakeHabiticaClient:
    return FakeHabiticaClient(tasks=[task_from_json(item) for item in load_data("tasks")])


async def records_for(text: str) -> dict[date, tuple[bool, bool]]:
    async with session_scope() as session:
        rows = await session.execute(
            select(DailyRecord)
            .join(TaskRow, TaskRow.id == DailyRecord.task_id)
            .where(TaskRow.text == text)
        )
        return {r.day: (r.was_due, r.completed) for (r,) in rows}


async def test_backfill_from_the_recorded_account(db_path: Path) -> None:
    client = fixture_client()
    async with session_scope() as session:
        await collect(client, session, USER, datetime(2026, 9, 11, 15, 0, tzinfo=UTC))
        outcome = await backfill(client, session, USER)

    milk = await records_for("Glass of milk")
    assert milk[date(2026, 8, 29)] == (True, False)  # damage at the 08-30 cron
    assert milk[date(2026, 8, 30)] == (True, False)
    assert milk[date(2026, 8, 31)] == (True, True)
    assert milk[date(2026, 9, 1)] == (True, False)
    assert milk[date(2026, 9, 2)] == (True, True)
    assert date(2026, 9, 3) not in milk  # no entry: unknown, not guessed
    assert milk[date(2026, 9, 8)] == (True, True)  # ticked 09-09 08:18Z, before dayStart
    assert milk[date(2026, 9, 10)] == (True, True)

    supplements = await records_for("Morning supplements")
    assert all(completed for _, completed in supplements.values())
    assert len(supplements) == 25

    async with session_scope() as session:
        habit_events = await session.execute(
            select(TaskRow.text, func.count(TaskEvent.id))
            .join(TaskEvent, TaskEvent.task_id == TaskRow.id)
            .where(TaskEvent.source == "backfill", TaskRow.type == "habit")
            .group_by(TaskRow.text)
        )
        daily_completed = await session.scalar(
            select(func.count(TaskEvent.id)).where(
                TaskEvent.source == "backfill", TaskEvent.kind == "completed"
            )
        )
    assert dict(habit_events.all()) == {"Complete a :tomato:!": 13, "1h of sun exposure": 1}
    ticks_in_fixture = sum(
        1
        for item in load_data("tasks")
        if item["type"] == "daily"
        for entry in item["history"]
        if entry["completed"]
    )
    assert daily_completed == ticks_in_fixture == 115
    assert len(outcome.events) == 14 + ticks_in_fixture


async def test_backfill_is_idempotent(db_path: Path) -> None:
    client = fixture_client()
    async with session_scope() as session:
        await collect(client, session, USER, datetime(2026, 9, 11, 15, 0, tzinfo=UTC))
        first = await backfill(client, session, USER)
    async with session_scope() as session:
        second = await backfill(client, session, USER)

    assert first.records and first.events
    assert second.records == [] and second.events == []
    async with session_scope() as session:
        records = await session.scalar(select(func.count(DailyRecord.id)))
        events = await session.scalar(
            select(func.count(TaskEvent.id)).where(TaskEvent.source == "backfill")
        )
    assert records == len(first.records)
    assert events == len(first.events)


async def test_backfill_never_overwrites_a_close_day_record(db_path: Path) -> None:
    client = fixture_client()
    async with session_scope() as session:
        await collect(client, session, USER, datetime(2026, 9, 11, 15, 0, tzinfo=UTC))
        milk = await session.scalar(select(TaskRow).where(TaskRow.text == "Glass of milk"))
        assert milk is not None
        session.add(
            DailyRecord(
                user_id=USER,
                task_id=milk.id,
                day=date(2026, 8, 29),
                was_due=True,
                completed=True,
                source="close_day",
            )
        )
    async with session_scope() as session:
        await backfill(client, session, USER)

    assert (await records_for("Glass of milk"))[date(2026, 8, 29)] == (True, True)
    assert (await records_for("Glass of milk"))[date(2026, 8, 30)] == (True, False)


def test_days_before_day_start_belong_to_the_previous_day_in_backfill() -> None:
    assert SUMMER.habitica_day(at(9, 8, 18)) == date(2026, 9, 8)
    assert SUMMER.habitica_day(at(9, 8, 18) + timedelta(hours=1)) == date(2026, 9, 9)
