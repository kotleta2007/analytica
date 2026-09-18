"""The daily reminder: once a day at the user's time, only what is still due."""

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.collector.snapshots import collect
from app.core.harness import Harness
from app.core.replies import User
from app.core.turn import load_settings
from app.db.engine import session_scope
from app.db.models import UserSettings
from app.jobs.reminders import reminder_due, reminder_reply
from tests.fakes.habitica import FakeHabiticaClient
from tests.task_factory import make_task, with_fields

USER = User("00000000-0000-4000-8000-000000000001")
ZURICH = ZoneInfo("Europe/Zurich")
AT_2100 = datetime(2026, 9, 12, 19, 0, tzinfo=UTC)  # 21:00 Zurich
AT_2030 = datetime(2026, 9, 12, 18, 30, tzinfo=UTC)


def settings(**fields: object) -> UserSettings:
    return UserSettings(user_id=USER.id, **{"reminder_time": "21:00", **fields})


def test_due_at_the_reminder_time_and_after_it_once_per_local_day() -> None:
    assert not reminder_due(settings(), AT_2030, ZURICH)
    assert reminder_due(settings(), AT_2100, ZURICH)
    assert reminder_due(settings(), datetime(2026, 9, 12, 21, 55, tzinfo=UTC), ZURICH)  # late
    assert not reminder_due(settings(last_reminder_day=date(2026, 9, 12)), AT_2100, ZURICH)
    assert reminder_due(settings(last_reminder_day=date(2026, 9, 11)), AT_2100, ZURICH)


def test_off_means_never() -> None:
    assert not reminder_due(settings(reminder_time=None), AT_2100, ZURICH)


@pytest.fixture
def client() -> FakeHabiticaClient:
    return FakeHabiticaClient(
        tasks=[
            make_task("daily", "Floss", completed=True, isDue=True),
            make_task("daily", "Gym", completed=False, isDue=True),
            make_task("daily", "Call mom", completed=False, isDue=False),
            make_task("habit", "Run"),
            make_task("todo", "Essay"),
        ]
    )


async def test_the_reminder_lists_only_due_and_incomplete_dailies_and_fires_once(
    db_path: Path, client: FakeHabiticaClient
) -> None:
    async with session_scope() as session:
        await collect(client, session, USER.id, AT_2030)
        assert await reminder_reply(client, session, USER.id, AT_2030, ZURICH) is None
        reply = await reminder_reply(client, session, USER.id, AT_2100, ZURICH)
        again = await reminder_reply(client, session, USER.id, AT_2100, ZURICH)

    assert reply is not None and reply.text == "Still due today:\n• Gym"
    assert again is None
    async with session_scope() as session:
        assert (await load_settings(session, USER.id)).last_reminder_day == date(2026, 9, 12)


async def test_a_stale_snapshot_is_refreshed_first(
    db_path: Path, client: FakeHabiticaClient
) -> None:
    async with session_scope() as session:
        await collect(client, session, USER.id, datetime(2026, 9, 12, 12, 0, tzinfo=UTC))
    client.replace_task(with_fields(client.find("daily-gym"), completed=True))  # done since

    async with session_scope() as session:
        reply = await reminder_reply(client, session, USER.id, AT_2100, ZURICH)

    assert reply is None  # nothing due after the refresh — and the day still counts as reminded
    assert client.called("get_tasks")  # refreshed


async def test_nothing_due_sends_nothing(db_path: Path, client: FakeHabiticaClient) -> None:
    client.tasks = [make_task("daily", "Floss", completed=True, isDue=True)]
    async with session_scope() as session:
        await collect(client, session, USER.id, AT_2100)
        assert await reminder_reply(client, session, USER.id, AT_2100, ZURICH) is None


async def test_remind_command_sets_shows_and_switches_off(
    db_path: Path, client: FakeHabiticaClient
) -> None:
    harness = Harness(habitica=client, tz=ZURICH)

    assert (await harness.command_received(USER, "remind", "")).text.startswith(
        "The daily reminder is at 21:00."
    )
    assert (await harness.command_received(USER, "remind", "22:15")).text == (
        "Daily reminder set to 22:15."
    )
    assert (await harness.command_received(USER, "remind", "9:5")).text == (
        "Daily reminder set to 09:05."
    )
    assert (await harness.command_received(USER, "remind", "noon")).text.startswith("Usage:")
    assert (await harness.command_received(USER, "remind", "off")).text == "Daily reminder off."
    assert (await harness.command_received(USER, "remind", "")).text.startswith(
        "The daily reminder is off."
    )
    async with session_scope() as session:
        assert (await load_settings(session, USER.id)).reminder_time is None
