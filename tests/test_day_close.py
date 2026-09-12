from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.collector.day_close import DayConfig, close_day, run_cron
from app.collector.snapshots import collect
from app.db.engine import session_scope
from app.db.models import DailyRecord, TaskRow, UserSnapshot
from app.habitica.types import user_stats_from_json
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task, with_fields

USER = "00000000-0000-4000-8000-000000000001"
SUMMER = DayConfig(day_start=11, timezone_offset=-120)  # Zurich, CEST
WINTER = DayConfig(day_start=11, timezone_offset=-60)  # Zurich, CET


def test_day_boundary_in_summer() -> None:
    assert SUMMER.habitica_day(datetime(2026, 9, 11, 9, 0, tzinfo=UTC)) == date(2026, 9, 11)
    assert SUMMER.habitica_day(datetime(2026, 9, 11, 8, 59, tzinfo=UTC)) == date(2026, 9, 10)
    assert SUMMER.day_start_utc(date(2026, 9, 11)) == datetime(2026, 9, 11, 9, 0, tzinfo=UTC)


def test_day_boundary_in_winter() -> None:
    assert WINTER.habitica_day(datetime(2026, 11, 5, 10, 0, tzinfo=UTC)) == date(2026, 11, 5)
    assert WINTER.habitica_day(datetime(2026, 11, 5, 9, 59, tzinfo=UTC)) == date(2026, 11, 4)
    assert WINTER.day_start_utc(date(2026, 11, 5)) == datetime(2026, 11, 5, 10, 0, tzinfo=UTC)


def test_the_boundary_moves_with_the_offset_across_dst() -> None:
    """Sunday 2026-10-25 02:00 CEST becomes 01:00 CET; Habitica sends the new offset."""
    before = DayConfig(11, -120).day_start_utc(date(2026, 10, 24))
    after = DayConfig(11, -60).day_start_utc(date(2026, 10, 25))
    assert before == datetime(2026, 10, 24, 9, 0, tzinfo=UTC)
    assert after == datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
    assert after - before == timedelta(hours=25)


def test_an_instant_before_midnight_local_still_belongs_to_the_same_day() -> None:
    late_evening = datetime(2026, 9, 11, 21, 30, tzinfo=UTC)  # 23:30 CEST
    early_morning = datetime(2026, 9, 12, 5, 0, tzinfo=UTC)  # 07:00 CEST next date
    assert SUMMER.habitica_day(late_evening) == date(2026, 9, 11)
    assert SUMMER.habitica_day(early_morning) == date(2026, 9, 11)
    assert SUMMER.next_day_start(early_morning) == datetime(2026, 9, 12, 9, 0, tzinfo=UTC)


def test_day_config_from_the_user_response() -> None:
    config = DayConfig.from_user_raw(load_data("user"))
    assert config == SUMMER


def test_config_with_a_midnight_day_start() -> None:
    midnight = DayConfig(day_start=0, timezone_offset=-120)
    assert midnight.habitica_day(datetime(2026, 9, 11, 22, 0, tzinfo=UTC)) == date(2026, 9, 12)
    assert midnight.habitica_day(datetime(2026, 9, 11, 21, 59, tzinfo=UTC)) == date(2026, 9, 11)


async def records_for(day: date) -> dict[str, tuple[bool, bool, str]]:
    async with session_scope() as session:
        rows = await session.execute(
            select(TaskRow.text, DailyRecord)
            .join(DailyRecord, DailyRecord.task_id == TaskRow.id)
            .where(DailyRecord.day == day)
        )
        return {text: (r.was_due, r.completed, r.source) for text, r in rows}


async def test_close_day_writes_one_record_per_daily(db_path: Path) -> None:
    client = FakeHabiticaClient(
        tasks=[
            make_task("daily", "Floss", completed=True, isDue=True),
            make_task("daily", "Gym", completed=False, isDue=True),
            make_task("daily", "Call mom", completed=False, isDue=False),
            make_task("habit", "Run"),
            make_task("todo", "Essay"),
        ]
    )
    close_at = datetime(2026, 9, 11, 8, 58, tzinfo=UTC)  # 10:58 CEST, dayStart − 2 min

    async with session_scope() as session:
        records = await close_day(client, session, USER, close_at)

    assert len(records) == 3
    assert await records_for(date(2026, 9, 10)) == {
        "Floss": (True, True, "close_day"),
        "Gym": (True, False, "close_day"),
        "Call mom": (False, False, "close_day"),
    }
    assert await records_for(date(2026, 9, 11)) == {}


async def test_close_day_twice_updates_instead_of_duplicating(db_path: Path) -> None:
    gym = make_task("daily", "Gym", completed=False, isDue=True)
    client = FakeHabiticaClient(tasks=[gym])
    close_at = datetime(2026, 9, 11, 8, 58, tzinfo=UTC)

    async with session_scope() as session:
        await close_day(client, session, USER, close_at)
    client.tasks = [with_fields(gym, completed=True)]
    async with session_scope() as session:
        await close_day(client, session, USER, close_at + timedelta(seconds=30))

    async with session_scope() as session:
        rows = list(await session.scalars(select(DailyRecord)))
    assert len(rows) == 1
    assert await records_for(date(2026, 9, 10)) == {"Gym": (True, True, "close_day")}


async def test_close_day_skips_deleted_dailies(db_path: Path) -> None:
    floss = make_task("daily", "Floss", completed=True)
    client = FakeHabiticaClient(tasks=[floss, make_task("daily", "Gym")])
    async with session_scope() as session:
        await collect(client, session, USER, datetime(2026, 9, 10, 12, 0, tzinfo=UTC))

    client.tasks = [floss]
    async with session_scope() as session:
        await close_day(client, session, USER, datetime(2026, 9, 11, 8, 58, tzinfo=UTC))

    assert set(await records_for(date(2026, 9, 10))) == {"Floss"}


async def test_run_cron_calls_habitica_and_snapshots_after(db_path: Path) -> None:
    floss = make_task("daily", "Floss", completed=False, isDue=True)
    client = FakeHabiticaClient(tasks=[floss])
    async with session_scope() as session:
        await collect(client, session, USER, datetime(2026, 9, 11, 8, 58, tzinfo=UTC))

    # what Habitica does at cron: damage for the missed daily, mana back, new lastCron
    user_raw = {**load_data("user"), "lastCron": "2026-09-11T09:01:00.000Z"}
    user_raw["stats"] = {**user_raw["stats"], "hp": 41.2, "mp": 438}
    client.user = user_stats_from_json(user_raw)
    client.tasks = [with_fields(floss, streak=0)]

    async with session_scope() as session:
        outcome = await run_cron(client, session, USER, datetime(2026, 9, 11, 9, 1, tzinfo=UTC))

    assert client.called("run_cron") == [None]
    assert client.calls.index(("run_cron", None)) < len(client.calls) - 1
    assert outcome.before is not None and outcome.before.hp == 49
    assert outcome.after.user.hp == 41.2
    assert round(outcome.hp_lost, 1) == 7.8
    assert outcome.after.cron_ran is True
    async with session_scope() as session:
        snapshots = list(await session.scalars(select(UserSnapshot).order_by(UserSnapshot.id)))
    assert [s.hp for s in snapshots] == [49, 41.2]
