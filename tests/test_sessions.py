"""Session lifecycle: idle limits, summaries, replay, the token budget, /new and /session."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.core import sessions
from app.core.harness import Harness
from app.core.i18n import t
from app.core.replies import User
from app.db.engine import session_scope
from app.db.models import ChatSession
from tests.fakes.habitica import FakeHabiticaClient
from tests.fakes.llm import FakeLlm, says

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)


class StubSummarizer:
    def __init__(self, text: str = "Talked about tasks.", fail: bool = False) -> None:
        self.text = text
        self.fail = fail
        self.requests: list[list[dict[str, Any]]] = []

    async def __call__(self, messages: list[dict[str, Any]]) -> str:
        self.requests.append(messages)
        if self.fail:
            raise RuntimeError("summary model down")
        return self.text


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def assistant(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


def tool_call(call_id: str, name: str = "get_stats") -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [
            {"id": call_id, "type": "function", "function": {"name": name, "arguments": "{}"}}
        ],
    }


def tool_result(call_id: str, content: str = '{"hp": 40}') -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


async def fill(chat: ChatSession, db: Any, *messages: dict[str, Any], at: datetime = NOW) -> None:
    for message in messages:
        direction = "in" if message["role"] in ("user", "tool") else "out"
        kind = "tool" if message["role"] == "tool" or message.get("tool_calls") else "text"
        await sessions.append(db, chat, at, direction, kind, message.get("content") or "", message)


# ---- lifecycle ----


async def test_first_message_opens_a_quick_session(db_path: Path) -> None:
    async with session_scope() as db:
        chat = await sessions.ensure_session(db, USER.id, NOW, None)

    assert (chat.kind, chat.topic, chat.started_at, chat.ended_at) == ("quick", None, NOW, None)


async def test_a_message_within_the_idle_limit_stays_in_the_session(db_path: Path) -> None:
    summarize = StubSummarizer()
    async with session_scope() as db:
        first = await sessions.ensure_session(db, USER.id, NOW, summarize)
        await fill(first, db, user("hi"), assistant("hello"))
        again = await sessions.ensure_session(db, USER.id, NOW + timedelta(minutes=15), summarize)

    assert again.id == first.id
    assert summarize.requests == []


async def test_sixteen_minutes_later_closes_with_a_summary_and_opens_a_new_one(
    db_path: Path,
) -> None:
    summarize = StubSummarizer("Discussed Latin; agreed on habit Latin vocab.")
    async with session_scope() as db:
        first = await sessions.ensure_session(db, USER.id, NOW, summarize)
        await fill(first, db, user("I want to learn Latin"), assistant("Great, a habit?"))
        later = NOW + timedelta(minutes=16)
        second = await sessions.ensure_session(db, USER.id, later, summarize)

    assert second.id != first.id
    async with session_scope() as db:
        closed = await db.get(ChatSession, first.id)
        assert closed is not None
        assert closed.ended_at == later
        assert closed.summary == "Discussed Latin; agreed on habit Latin vocab."
        assert await sessions.recent_summaries(db, USER.id) == [closed.summary]
    (request,) = summarize.requests
    assert request[0]["role"] == "system" and "three" in request[0]["content"]
    assert request[1]["content"] == "User: I want to learn Latin\nCoach: Great, a habit?"


async def test_planning_sessions_survive_longer(db_path: Path) -> None:
    async with session_scope() as db:
        planning = await sessions.open_session(db, USER.id, NOW, sessions.PLANNING, "Latin")
        await fill(planning, db, user("let's plan"))
        same = await sessions.ensure_session(db, USER.id, NOW + timedelta(minutes=100), None)
        other = await sessions.ensure_session(db, USER.id, NOW + timedelta(minutes=121), None)

    assert same.id == planning.id
    assert other.id != planning.id and other.kind == "quick"


async def test_empty_session_closes_without_asking_for_a_summary(db_path: Path) -> None:
    summarize = StubSummarizer()
    async with session_scope() as db:
        chat = await sessions.open_session(db, USER.id, NOW)
        await sessions.close_session(db, chat, NOW, summarize)

    assert chat.ended_at == NOW and chat.summary is None
    assert summarize.requests == []


async def test_summary_failure_still_closes_the_session(
    db_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async with session_scope() as db:
        chat = await sessions.open_session(db, USER.id, NOW)
        await fill(chat, db, user("hi"))
        await sessions.close_session(db, chat, NOW, StubSummarizer(fail=True))

    assert chat.ended_at == NOW and chat.summary is None
    assert "summary failed" in caplog.text


async def test_recent_summaries_are_the_last_three_most_recent_first(db_path: Path) -> None:
    async with session_scope() as db:
        for n in range(5):
            chat = await sessions.open_session(db, USER.id, NOW + timedelta(hours=n))
            await fill(chat, db, user(f"message {n}"))
            await sessions.close_session(
                db, chat, NOW + timedelta(hours=n, minutes=30), StubSummarizer(f"summary {n}")
            )
        summaries = await sessions.recent_summaries(db, USER.id)

    assert summaries == ["summary 4", "summary 3", "summary 2"]


# ---- replay ----


async def test_replay_stubs_tool_results_of_earlier_turns_only(db_path: Path) -> None:
    async with session_scope() as db:
        chat = await sessions.open_session(db, USER.id, NOW)
        await fill(
            chat,
            db,
            user("stats?"),
            tool_call("c1"),
            tool_result("c1"),
            assistant("HP 40."),
            user("and now?"),
            tool_call("c2"),
            tool_result("c2", '{"hp": 39}'),
        )
        replayed = await sessions.replay(db, chat)

    assert replayed == [
        user("stats?"),
        tool_call("c1"),
        tool_result("c1", sessions.TOOL_RESULT_STUB),
        assistant("HP 40."),
        user("and now?"),
        tool_call("c2"),
        tool_result("c2", '{"hp": 39}'),
    ]


def test_orphaned_tool_calls_are_dropped_or_reduced_to_their_text() -> None:
    messages = [
        user("go"),
        tool_call("lost"),
        {**tool_call("lost2"), "content": "Let me check."},
        tool_call("ok"),
        tool_result("ok"),
    ]

    assert sessions.drop_orphaned_tool_calls(messages) == [
        user("go"),
        assistant("Let me check."),
        tool_call("ok"),
        tool_result("ok"),
    ]


# ---- token budget ----


async def test_over_budget_session_is_collapsed_at_a_user_message(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("SESSION_TOKEN_BUDGET", "60")
    get_settings.cache_clear()
    summarize = StubSummarizer("Earlier: the user asked for stats twice.")
    async with session_scope() as db:
        chat = await sessions.open_session(db, USER.id, NOW)
        await fill(
            chat,
            db,
            user("stats?"),
            tool_call("c1"),
            tool_result("c1"),
            assistant("HP 40."),
            user("again"),
            assistant("Still 40."),
            user("thanks"),
            assistant("Sure."),
        )
        collapsed = await sessions.collapse_if_over_budget(db, chat, summarize)
        replayed = await sessions.replay(db, chat)

    assert collapsed is True
    assert chat.collapsed_summary == "Earlier: the user asked for stats twice."
    # 8 messages, midpoint 4 = the user message "again": the cut lands there.
    assert replayed == [user("again"), assistant("Still 40."), user("thanks"), assistant("Sure.")]
    (request,) = summarize.requests
    assert "Coach called get_stats({})" in request[1]["content"]
    assert "User: again" not in request[1]["content"]


async def test_second_collapse_carries_the_earlier_summary(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.config import get_settings

    monkeypatch.setenv("SESSION_TOKEN_BUDGET", "30")
    get_settings.cache_clear()
    summarize = StubSummarizer("second summary")
    async with session_scope() as db:
        chat = await sessions.open_session(db, USER.id, NOW)
        chat.collapsed_summary = "first summary"
        await fill(chat, db, user("a" * 60), assistant("b" * 60), user("c"), assistant("d"))
        await sessions.collapse_if_over_budget(db, chat, summarize)

    assert chat.collapsed_summary == "second summary"
    assert summarize.requests[0][1]["content"].startswith("Summary so far: first summary\n")


async def test_under_budget_or_without_a_summarizer_nothing_is_collapsed(db_path: Path) -> None:
    async with session_scope() as db:
        chat = await sessions.open_session(db, USER.id, NOW)
        await fill(chat, db, user("hi"), assistant("hello"))

        assert await sessions.collapse_if_over_budget(db, chat, StubSummarizer()) is False
        assert await sessions.collapse_if_over_budget(db, chat, None) is False
    assert chat.collapsed_summary is None


# ---- /new and /session ----


@pytest.fixture
async def harness(db_path: Path) -> Harness:
    fake_llm = FakeLlm(says("Discussed nothing much."))
    return Harness(
        habitica=FakeHabiticaClient(),  # type: ignore[arg-type]
        llm=fake_llm,  # type: ignore[arg-type]
        clock=lambda: NOW,
        tz=ZoneInfo("Europe/Zurich"),
    )


async def test_session_command_without_a_session(harness: Harness) -> None:
    reply = await harness.command_received(USER, "session", "")

    assert reply.text == t("session_none", "en")


async def test_new_closes_immediately_with_a_summary_from_the_cheap_model(
    harness: Harness,
) -> None:
    async with session_scope() as db:
        chat = await sessions.open_session(db, USER.id, NOW - timedelta(minutes=5))
        await fill(chat, db, user("hello"), assistant("hi"), at=NOW - timedelta(minutes=5))

    shown = await harness.command_received(USER, "session", "")
    closed = await harness.command_received(USER, "new", "")
    none = await harness.command_received(USER, "new", "")

    assert shown.text == "Quick session · started 5 min ago · last message 5 min ago · 2 messages"
    assert closed.text == t("session_closed", "en")
    assert none.text == t("session_none", "en")
    async with session_scope() as db:
        row = await db.scalar(select(ChatSession))
        assert row is not None
        assert (row.ended_at, row.summary) == (NOW, "Discussed nothing much.")
    fake_llm: FakeLlm = harness.llm  # type: ignore[assignment]
    assert fake_llm.calls[0].model == "deepseek/deepseek-v4-flash"


async def test_session_planning_opens_a_topic_session(harness: Harness) -> None:
    await harness.command_received(USER, "language", "fr")
    needs_topic = await harness.command_received(USER, "session", "planning")
    opened = await harness.command_received(USER, "session", "planning apprendre le latin")
    shown = await harness.command_received(USER, "session", "")

    assert needs_topic.text == t("session_planning_needs_topic", "fr")
    assert opened.text == t("session_planning_opened", "fr", topic="apprendre le latin")
    assert shown.text.startswith("session de planification — apprendre le latin · ")
    async with session_scope() as db:
        row = await db.scalar(select(ChatSession))
        assert row is not None and (row.kind, row.topic) == ("planning", "apprendre le latin")
