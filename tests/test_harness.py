from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy import select

from app.core import outbound
from app.core.commands import fmt, parse_command
from app.core.harness import Harness
from app.core.i18n import t
from app.core.replies import Reply, User
from app.db.engine import session_scope
from app.db.models import Message, UserSettings, UserSnapshot
from app.habitica.types import class_label, task_from_json
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 12, 12, 30, tzinfo=UTC)  # 14:30 in Zurich


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def fake_client() -> FakeHabiticaClient:
    return FakeHabiticaClient(tasks=[task_from_json(item) for item in load_data("tasks")])


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
async def harness(db_path: Path, fake_client: FakeHabiticaClient, clock: Clock) -> Harness:
    return Harness(habitica=fake_client, clock=clock, tz=ZoneInfo("Europe/Zurich"))


async def logged_messages() -> list[tuple[str, str, str]]:
    async with session_scope() as session:
        rows = await session.scalars(select(Message).order_by(Message.id))
        return [(row.direction, row.kind, row.text) for row in rows]


# ---- parsing ----


def test_parse_command_splits_command_and_args() -> None:
    assert parse_command("/language fr") == ("language", "fr")
    assert parse_command("  /Stats  ") == ("stats", "")
    assert parse_command("/start@AnalyticaBot") == ("start", "")
    assert parse_command("/target Workout 3") == ("target", "Workout 3")


def test_parse_command_rejects_plain_text() -> None:
    assert parse_command("hello") is None
    assert parse_command("/") is None
    assert parse_command("") is None


def test_fmt_hides_a_zero_fraction_only() -> None:
    assert fmt(49.0) == "49"
    assert fmt(42.66) == "42.7"


# ---- commands and language ----


async def test_language_persists_and_help_follows_it(harness: Harness) -> None:
    reply = await harness.command_received(USER, "language", "fr")
    assert reply.text == t("language_set", "fr")

    async with session_scope() as session:
        settings = await session.get(UserSettings, USER.id)
    assert settings is not None and settings.language == "fr"

    help_reply = await harness.command_received(USER, "help", "")
    assert help_reply.text.startswith("Commandes :")
    assert "/stats — tes stats Habitica" in help_reply.text


async def test_language_without_args_shows_the_current_one(harness: Harness) -> None:
    reply = await harness.command_received(USER, "language", "")

    assert reply.text == t("language_current", "en")


async def test_invalid_language_is_refused_and_not_stored(harness: Harness) -> None:
    reply = await harness.command_received(USER, "language", "de")

    assert reply.text == t("language_invalid", "en")
    async with session_scope() as session:
        settings = await session.get(UserSettings, USER.id)
    assert settings is not None and settings.language == "en"


async def test_unknown_command_gets_the_i18n_line(harness: Harness) -> None:
    await harness.command_received(USER, "language", "ru")

    reply = await harness.command_received(USER, "plan", "3")

    assert reply.text == t("unknown_command", "ru", command="plan")


async def test_start_greets_and_lists_commands(harness: Harness) -> None:
    reply = await harness.command_received(USER, "start", "")

    assert reply.text.startswith(t("welcome", "en"))
    assert "/language en|ru|fr — reply language" in reply.text


async def test_free_text_and_voice_are_declined_without_openrouter(harness: Harness) -> None:
    text_reply = await harness.text_message_received(USER, "complete workout")
    voice_reply = await harness.voice_message_received(USER, b"\x00\x01", "audio/ogg")

    assert text_reply.text == t("llm_not_configured", "en")
    assert voice_reply.text == t("voice_not_configured", "en")


async def test_empty_text_means_an_unreadable_message(harness: Harness) -> None:
    reply = await harness.text_message_received(USER, "  ")

    assert reply.text == t("unsupported_message", "en")


async def test_unknown_button_is_reported(harness: Harness) -> None:
    reply = await harness.button_pressed(USER, "confirm", {"id": 1})

    assert reply.text == t("unknown_button", "en")


async def test_messages_are_logged_in_both_directions(harness: Harness) -> None:
    await harness.command_received(USER, "language", "ru")
    await harness.text_message_received(USER, "привет")
    await harness.button_pressed(USER, "confirm", {})

    assert await logged_messages() == [
        ("in", "command", "/language ru"),
        ("out", "text", t("language_set", "ru")),
        ("in", "text", "привет"),
        ("out", "text", t("llm_not_configured", "ru")),
        ("in", "button", "confirm"),
        ("out", "text", t("unknown_button", "ru")),
    ]


# ---- /stats and /sync ----


async def test_stats_collects_when_there_is_no_snapshot(
    harness: Harness, fake_client: FakeHabiticaClient
) -> None:
    await harness.command_received(USER, "language", "ru")

    reply = await harness.command_received(USER, "stats", "")

    user = fake_client.user
    assert reply.text.startswith(f"❤️ {user.hp:.0f}/{user.max_hp}  🔵 {user.mp:.0f}/{user.max_mp}")
    assert f"Уровень {user.lvl}, {class_label(user.class_name)} · на 14:30" in reply.text
    assert fake_client.called("get_user") == [None]
    async with session_scope() as session:
        assert await session.scalar(select(UserSnapshot)) is not None


async def test_stats_reuses_a_fresh_snapshot_and_refreshes_a_stale_one(
    harness: Harness, fake_client: FakeHabiticaClient, clock: Clock
) -> None:
    await harness.command_received(USER, "stats", "")
    clock.now = NOW + timedelta(minutes=5)
    await harness.command_received(USER, "stats", "")
    assert len(fake_client.called("get_user")) == 1

    clock.now = NOW + timedelta(minutes=11)
    reply = await harness.command_received(USER, "stats", "")

    assert len(fake_client.called("get_user")) == 2
    assert "as of 14:41" in reply.text


async def test_sync_always_collects_and_reports_the_round(
    harness: Harness, fake_client: FakeHabiticaClient
) -> None:
    first = await harness.command_received(USER, "sync", "")
    second = await harness.command_received(USER, "sync", "")

    tasks = len(fake_client.tasks)
    assert first.text.startswith(f"Synced with Habitica: {tasks} tasks, {tasks} changed, ")
    assert second.text.startswith(f"Synced with Habitica: {tasks} tasks, 0 changed, 0 events.")
    assert "Level" in second.text
    assert len(fake_client.called("get_user")) == 2


# ---- failures ----


async def test_habitica_failure_becomes_a_reply(
    harness: Harness, fake_client: FakeHabiticaClient
) -> None:
    async def broken_get_user() -> None:
        raise httpx.ConnectError("connection refused")

    fake_client.get_user = broken_get_user  # type: ignore[method-assign]

    reply = await harness.command_received(USER, "sync", "")

    assert reply.text == t("habitica_error", "en", error="connection refused")
    assert await logged_messages() == [("in", "command", "/sync"), ("out", "text", reply.text)]


async def test_unexpected_error_is_logged_and_answered(
    harness: Harness, fake_client: FakeHabiticaClient, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken_get_user() -> None:
        raise RuntimeError("boom")

    fake_client.get_user = broken_get_user  # type: ignore[method-assign]

    reply = await harness.command_received(USER, "stats", "")

    assert reply.text == t("something_went_wrong", "en")
    assert "command turn failed" in caplog.text
    assert await logged_messages() == [("in", "command", "/stats"), ("out", "text", reply.text)]


# ---- outbound ----


@pytest.fixture
async def no_hook() -> AsyncIterator[None]:
    outbound.unregister()
    yield
    outbound.unregister()


async def test_outbound_without_an_adapter_is_dropped_and_reported(
    db_path: Path, no_hook: None, caplog: pytest.LogCaptureFixture
) -> None:
    delivered = await outbound.send_to_user(USER, Reply("You still have 2 dailies"))

    assert delivered is False
    assert "no adapter registered" in caplog.text
    assert await logged_messages() == []


async def test_outbound_goes_through_the_hook_and_is_logged(db_path: Path, no_hook: None) -> None:
    sent: list[tuple[User, Reply]] = []

    async def hook(user: User, reply: Reply) -> None:
        sent.append((user, reply))

    outbound.register(hook)
    delivered = await outbound.send_to_user(USER, Reply("You still have 2 dailies"))

    assert delivered is True
    assert sent == [(USER, Reply("You still have 2 dailies"))]
    assert await logged_messages() == [("out", "text", "You still have 2 dailies")]
