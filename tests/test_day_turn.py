"""The day-turn window: the question about unticked dailies and its two buttons.

Times: the fixture user has dayStart 11 and Zurich summer time, so the
Habitica day turns at 09:00 UTC; the bot's own cron is due at 09:01 UTC.
"""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.collector.day_close import cron_hold, set_cron_hold
from app.collector.snapshots import collect
from app.core.day_turn import (
    DONE_ACTION,
    TICK_ACTION,
    Window,
    ask,
    day_has_turned,
    unticked_dailies,
    window,
)
from app.core.harness import Harness
from app.core.replies import User
from app.core.tasks import load_known_tasks
from app.db.engine import session_scope
from app.db.models import DailyRecord, TaskEvent, TaskRow
from app.habitica.types import user_stats_from_json
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task, with_fields

USER = User("00000000-0000-4000-8000-000000000001")
ZURICH = ZoneInfo("Europe/Zurich")
ASK_AT = datetime(2026, 9, 12, 8, 30, tzinfo=UTC)  # 10:30 Zurich
AFTER_START = datetime(2026, 9, 12, 9, 20, tzinfo=UTC)  # 11:20 Zurich
ENDING = date(2026, 9, 11)  # the fixture user last cronned on the 11th at 12:07 Zurich
GRACE = timedelta(minutes=90)


def user_raw(**fields: Any) -> dict[str, Any]:
    return {**load_data("user"), **fields}


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def milk_and_supplements() -> FakeHabiticaClient:
    return FakeHabiticaClient(
        tasks=[
            make_task("daily", "Glass of milk", completed=False, isDue=True),
            make_task("daily", "Evening supplements", completed=False, isDue=True),
            make_task("daily", "Floss", completed=True, isDue=True),
            make_task("daily", "Call mom", completed=False, isDue=False),
            make_task("daily", "Journal", completed=False, isDue=True, yesterDaily=False),
            make_task("habit", "Run"),
            make_task("todo", "Essay"),
        ]
    )


@pytest.fixture
def clock() -> Clock:
    return Clock(ASK_AT)


@pytest.fixture
async def harness(db_path: Path, milk_and_supplements: FakeHabiticaClient, clock: Clock) -> Harness:
    async with session_scope() as session:
        await collect(milk_and_supplements, session, USER.id, ASK_AT)
    return Harness(habitica=milk_and_supplements, clock=clock, tz=ZURICH)


async def known() -> list:
    async with session_scope() as session:
        return await load_known_tasks(session, USER.id)


def press(task_id: str | None = None) -> dict[str, Any]:
    payload = {"day": ENDING.isoformat()}
    if task_id is not None:
        payload["task_id"] = task_id
    return payload


async def records_for(day: date) -> dict[str, bool]:
    async with session_scope() as session:
        rows = await session.execute(
            select(TaskRow.text, DailyRecord.completed)
            .join(DailyRecord, DailyRecord.task_id == TaskRow.id)
            .where(DailyRecord.day == day)
        )
        return {text: completed for text, completed in rows}


# ---- what is asked ----


async def test_unticked_means_due_not_done_and_not_opted_out(harness: Harness) -> None:
    """Floss is done, Call mom is not due, Journal has 'record yesterday' switched off."""
    assert [k.task.text for k in unticked_dailies(await known())] == [
        "Glass of milk",
        "Evening supplements",
    ]


def test_the_window_brackets_the_day_that_is_ending() -> None:
    before = window(user_raw(needsCron=False), ASK_AT, GRACE)
    assert before == Window(
        day=ENDING,
        cron_at=datetime(2026, 9, 12, 9, 1, tzinfo=UTC),
        hold_until=datetime(2026, 9, 12, 10, 30, tzinfo=UTC),
    )
    # After day start the account still waits for cron: same window, same ending day.
    assert window(user_raw(needsCron=True), AFTER_START, GRACE) == before


def test_the_day_has_turned_once_last_cron_is_past_its_end() -> None:
    assert not day_has_turned(user_raw(lastCron="2026-09-11T09:01:00.000Z"), ENDING)
    assert day_has_turned(user_raw(lastCron="2026-09-12T09:15:00.000Z"), ENDING)
    assert not day_has_turned(user_raw(lastCron=None), ENDING)


async def test_the_question_lists_the_dailies_with_one_button_each_and_nothing_else(
    harness: Harness,
) -> None:
    dailies = unticked_dailies(await known())
    reply = ask(dailies, window(user_raw(), ASK_AT, GRACE), "en", ZURICH)

    assert "still unticked:\n• Glass of milk\n• Evening supplements" in reply.text
    assert "turns at 11:01" in reply.text and "until 12:30" in reply.text
    assert [b.label for b in reply.buttons] == [
        "✅ Glass of milk",
        "✅ Evening supplements",
        "Nothing else",
    ]
    assert reply.buttons[0].action == TICK_ACTION
    assert reply.buttons[0].payload == {"day": "2026-09-11", "task_id": "daily-glass-of-milk"}
    assert reply.buttons[-1].action == DONE_ACTION
    assert reply.buttons[-1].payload == {"day": "2026-09-11"}


def test_the_question_speaks_the_users_language() -> None:
    dailies: list = []
    reply = ask(dailies, window(user_raw(), ASK_AT, GRACE), "ru", ZURICH)
    assert "в 11:01" in reply.text and reply.buttons[-1].label == "Больше ничего"
    reply = ask(dailies, window(user_raw(), ASK_AT, GRACE), "fr", ZURICH)
    assert "à 11:01" in reply.text and reply.buttons[-1].label == "Rien d'autre"


# ---- the buttons ----


async def test_a_tick_before_day_start_scores_and_asks_again_with_the_rest(
    harness: Harness, milk_and_supplements: FakeHabiticaClient
) -> None:
    async with session_scope() as session:
        await set_cron_hold(session, USER.id, datetime(2026, 9, 12, 10, 30, tzinfo=UTC))

    reply = await harness.button_pressed(USER, TICK_ACTION, press("daily-glass-of-milk"))

    assert milk_and_supplements.called("score_task") == [("daily-glass-of-milk", "up")]
    assert milk_and_supplements.called("run_cron") == []
    assert reply.text == "✅ Glass of milk — ticked.\nStill unticked:\n• Evening supplements"
    assert [b.label for b in reply.buttons] == ["✅ Evening supplements", "Nothing else"]
    async with session_scope() as session:
        # Not answered yet: the hold stands until the last one or "Nothing else".
        assert await cron_hold(session, USER.id) is not None
        events = list(await session.scalars(select(TaskEvent).where(TaskEvent.source == "bot")))
    assert events, "the tick is the bot's own task_event"


async def test_the_last_tick_before_day_start_releases_the_hold_and_waits_for_the_schedule(
    harness: Harness, milk_and_supplements: FakeHabiticaClient
) -> None:
    async with session_scope() as session:
        await set_cron_hold(session, USER.id, datetime(2026, 9, 12, 10, 30, tzinfo=UTC))
    await harness.button_pressed(USER, TICK_ACTION, press("daily-glass-of-milk"))

    reply = await harness.button_pressed(USER, TICK_ACTION, press("daily-evening-supplements"))

    assert reply.text == "✅ Evening supplements — ticked.\nOK — the day turns at 11:01."
    assert reply.buttons == []
    assert milk_and_supplements.called("run_cron") == []
    async with session_scope() as session:
        assert await cron_hold(session, USER.id) is None


async def test_the_last_tick_after_day_start_turns_the_day_at_once(
    harness: Harness, milk_and_supplements: FakeHabiticaClient, clock: Clock
) -> None:
    """Scoring while `needsCron` is set counts for the ending day; then cron, and the record
    for that day says completed — the streak the blind cron used to zero."""
    client = milk_and_supplements
    client.replace_task(with_fields(client.find("daily-evening-supplements"), completed=True))
    client.user = user_stats_from_json(user_raw(needsCron=True))
    clock.now = AFTER_START
    async with session_scope() as session:
        await set_cron_hold(session, USER.id, datetime(2026, 9, 12, 10, 30, tzinfo=UTC))

    reply = await harness.button_pressed(USER, TICK_ACTION, press("daily-glass-of-milk"))

    assert client.called("score_task") == [("daily-glass-of-milk", "up")]
    assert client.called("run_cron") == [None]
    assert client.calls.index(("score_task", ("daily-glass-of-milk", "up"))) < client.calls.index(
        ("run_cron", None)
    )
    assert reply.text.startswith("✅ Glass of milk — ticked.\nThe day has turned.")
    assert (await records_for(ENDING))["Glass of milk"] is True
    assert await records_for(date(2026, 9, 12)) == {}
    async with session_scope() as session:
        assert await cron_hold(session, USER.id) is None


async def test_nothing_else_after_day_start_turns_the_day_at_once(
    harness: Harness, milk_and_supplements: FakeHabiticaClient, clock: Clock
) -> None:
    client = milk_and_supplements
    client.user = user_stats_from_json(user_raw(needsCron=True))
    clock.now = AFTER_START

    reply = await harness.button_pressed(USER, DONE_ACTION, press())

    assert client.called("score_task") == []
    assert client.called("run_cron") == [None]
    assert reply.text.startswith("The day has turned.")
    assert (await records_for(ENDING))["Glass of milk"] is False


async def test_nothing_else_before_day_start_only_releases_the_hold(
    harness: Harness, milk_and_supplements: FakeHabiticaClient
) -> None:
    async with session_scope() as session:
        await set_cron_hold(session, USER.id, datetime(2026, 9, 12, 10, 30, tzinfo=UTC))

    reply = await harness.button_pressed(USER, DONE_ACTION, press())

    assert reply.text == "OK — the day turns at 11:01."
    assert milk_and_supplements.called("run_cron") == []
    async with session_scope() as session:
        assert await cron_hold(session, USER.id) is None


async def test_a_tick_after_the_day_turned_is_refused_and_scores_nothing(
    harness: Harness, milk_and_supplements: FakeHabiticaClient, clock: Clock
) -> None:
    """Ticking now would complete the daily for today, not for the day that was asked about."""
    client = milk_and_supplements
    client.user = user_stats_from_json(user_raw(lastCron="2026-09-12T09:15:00.000Z"))
    clock.now = datetime(2026, 9, 12, 10, 40, tzinfo=UTC)

    reply = await harness.button_pressed(USER, TICK_ACTION, press("daily-glass-of-milk"))

    assert client.called("score_task") == []
    assert reply.text == (
        "Too late — the day already turned at 11:15. Anything ticked now counts for today."
    )
    assert reply.buttons == []


async def test_a_tick_for_a_task_that_is_gone_is_an_unknown_button(
    harness: Harness, milk_and_supplements: FakeHabiticaClient
) -> None:
    reply = await harness.button_pressed(USER, TICK_ACTION, press("daily-deleted-meanwhile"))

    assert milk_and_supplements.called("score_task") == []
    assert "button" in reply.text.lower()
