"""The free-text turn end to end, with a scripted model and a fake Habitica."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.collector.snapshots import collect
from app.core import conversation
from app.core.harness import Harness
from app.core.i18n import t
from app.core.replies import User
from app.db.engine import session_scope
from app.db.models import ChatSession, Message, TaskEvent, TaskRow
from app.llm.client import LlmError, ToolCall
from tests.fakes.habitica import FakeHabiticaClient
from tests.fakes.llm import FakeLlm, calls_tool, calls_tools, says
from tests.task_factory import make_task

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Zurich")

DAILY = make_task("daily", "Vacuum the floors", task_id="d1a2b3c4-0000-4000-8000-000000000001")
HABIT = make_task("habit", "Workout", task_id="ab1a2b3c-0000-4000-8000-000000000002")


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def habitica() -> FakeHabiticaClient:
    return FakeHabiticaClient(tasks=[DAILY, HABIT])


@pytest.fixture
def llm() -> FakeLlm:
    return FakeLlm()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
async def harness(
    db_path: Path, habitica: FakeHabiticaClient, llm: FakeLlm, clock: Clock
) -> Harness:
    async with session_scope() as db:
        await collect(habitica, db, USER.id, NOW - timedelta(minutes=1))
    habitica.calls.clear()
    return Harness(habitica=habitica, llm=llm, clock=clock, tz=TZ)  # type: ignore[arg-type]


async def bot_events() -> list[tuple[str, str]]:
    async with session_scope() as db:
        pairs = await db.execute(
            select(TaskRow.text, TaskEvent.kind)
            .join(TaskEvent, TaskEvent.task_id == TaskRow.id)
            .where(TaskEvent.source == "bot")
            .order_by(TaskEvent.id)
        )
        return [tuple(row) for row in pairs.all()]


async def session_rows() -> list[tuple[str, str, str, Any]]:
    async with session_scope() as db:
        rows = await db.scalars(select(Message).order_by(Message.id))
        return [(r.direction, r.kind, r.text, r.llm) for r in rows]


# ---- the tool loop ----


async def test_a_tool_call_is_dispatched_and_the_answer_comes_back(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(
        calls_tool("score_task", task_id="d1a2b3c4", direction="up"),
        says("Done — Vacuum the floors is ticked off."),
    )

    reply = await harness.text_message_received(USER, "j'ai passé l'aspirateur")

    assert reply.text == "Done — Vacuum the floors is ticked off."
    assert habitica.called("score_task") == [(DAILY.id, "up")]
    assert len(llm.calls) == 2
    # The second call replays the first one's tool call and its result.
    replayed = llm.calls[1].messages
    assert replayed[-2]["role"] == "assistant" and replayed[-2]["tool_calls"]
    assert replayed[-1]["role"] == "tool"
    assert "completed" in replayed[-1]["content"]


async def test_a_task_changing_tool_triggers_a_bot_sourced_collection(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("score_task", task_id="d1a2b3c4", direction="up"), says("Done."))

    await harness.text_message_received(USER, "vacuumed")

    assert await bot_events() == [("Vacuum the floors", "completed")]
    assert habitica.called("get_tasks")  # the snapshot after the change


async def test_a_read_only_tool_does_not_collect(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("list_tasks"), says("You have two tasks."))

    await harness.text_message_received(USER, "what do I have?")

    assert habitica.called("get_tasks") == []
    assert await bot_events() == []


async def test_several_tool_calls_in_one_round(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(
        calls_tools(
            [
                ToolCall("c1", "score_task", {"task_id": "d1a2b3c4", "direction": "up"}),
                ToolCall("c2", "score_task", {"task_id": "ab1a2b3c", "direction": "up"}),
            ]
        ),
        says("Both done."),
    )

    reply = await harness.text_message_received(USER, "vacuumed and worked out")

    assert reply.text == "Both done."
    assert habitica.called("score_task") == [(DAILY.id, "up"), (HABIT.id, "up")]
    tool_results = [m for m in llm.calls[1].messages if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_results] == ["c1", "c2"]


async def test_the_loop_stops_after_six_rounds_and_forces_a_text_answer(
    harness: Harness, llm: FakeLlm, caplog: pytest.LogCaptureFixture
) -> None:
    llm.queue(*[calls_tool("list_tasks", call_id=f"c{n}") for n in range(6)])
    llm.queue(says("Here is what I found."))

    reply = await harness.text_message_received(USER, "keep looking")

    assert reply.text == "Here is what I found."
    assert len(llm.calls) == 7
    assert llm.calls[-1].tool_choice == "none"
    assert "tool rounds exhausted" in caplog.text


async def test_an_empty_answer_is_reported(harness: Harness, llm: FakeLlm) -> None:
    llm.queue(says("   "))

    reply = await harness.text_message_received(USER, "hi")

    assert reply.text == t("llm_empty_reply", "en")


async def test_a_model_failure_becomes_a_reply(harness: Harness, llm: FakeLlm) -> None:
    llm.queue(LlmError("z-ai/glm-5.3: HTTP 402: out of credits"))

    reply = await harness.text_message_received(USER, "hi")

    assert reply.text == t("llm_error", "en", error="z-ai/glm-5.3: HTTP 402: out of credits")


# ---- prompt and model choice ----


async def test_the_prompt_carries_the_language_stats_and_task_list(
    harness: Harness, llm: FakeLlm
) -> None:
    await harness.command_received(USER, "language", "ru")
    llm.queue(says("Привет."))

    await harness.text_message_received(USER, "привет")

    system = llm.calls[0].system
    assert "Reply in Russian." in system
    assert "- d1a2b3c4 daily · Vacuum the floors" in system
    assert "Stats: HP" in system
    assert llm.calls[0].messages[-1] == {"role": "user", "content": "привет"}
    assert llm.calls[0].tool_names == [
        "list_tasks",
        "score_task",
        "create_task",
        "update_task",
        "delete_task",
        "get_stats",
    ]


async def test_the_configured_strong_model_is_used_by_default(
    harness: Harness, llm: FakeLlm
) -> None:
    llm.queue(says("Hi."))

    await harness.text_message_received(USER, "hi")

    assert llm.calls[0].model == "z-ai/glm-5.3"


# ---- sessions ----


async def test_the_turn_is_stored_in_a_session_and_replayed_next_time(
    harness: Harness, llm: FakeLlm, clock: Clock
) -> None:
    llm.queue(says("Hello!"), says("Still here."))

    await harness.text_message_received(USER, "hi")
    clock.now = NOW + timedelta(minutes=5)
    await harness.text_message_received(USER, "you there?")

    assert [m["content"] for m in llm.calls[1].messages if m["role"] != "system"] == [
        "hi",
        "Hello!",
        "you there?",
    ]
    async with session_scope() as db:
        assert len(list(await db.scalars(select(ChatSession)))) == 1


async def test_a_new_session_starts_after_the_idle_limit(
    harness: Harness, llm: FakeLlm, clock: Clock
) -> None:
    llm.queue(says("Hello!"), says("Summary line."), says("Fresh start."))

    await harness.text_message_received(USER, "hi")
    clock.now = NOW + timedelta(minutes=16)
    await harness.text_message_received(USER, "hello again")

    # The middle call is the cheap model writing the closed session's summary.
    assert llm.calls[1].model == "deepseek/deepseek-v4-flash"
    assert "Recent context, most recent first:\n- Summary line." in llm.calls[2].system
    assert [m["content"] for m in llm.calls[2].messages if m["role"] == "user"] == ["hello again"]


async def test_tool_rounds_are_logged_as_session_messages(harness: Harness, llm: FakeLlm) -> None:
    llm.queue(calls_tool("list_tasks"), says("Two tasks."))

    await harness.text_message_received(USER, "what do I have?")

    rows = await session_rows()
    assert [(direction, kind) for direction, kind, _, _ in rows] == [
        ("in", "text"),
        ("out", "tool"),
        ("in", "tool"),
        ("out", "text"),
    ]
    assert rows[1][2] == "list_tasks()"
    assert rows[1][3]["tool_calls"][0]["function"]["name"] == "list_tasks"
    assert rows[3][3] == {"role": "assistant", "content": "Two tasks."}


# ---- confirmation ----


async def test_delete_asks_then_the_button_deletes(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(
        calls_tool("delete_task", task_id="ab1a2b3c"),
        says("I'll delete Workout if you confirm."),
    )

    asked = await harness.text_message_received(USER, "delete the workout habit")

    assert habitica.called("delete_task") == []
    assert asked.text == "I'll delete Workout if you confirm."
    assert [b.label for b in asked.buttons] == ["✅ Do it", "❌ Cancel"]

    done = await harness.button_pressed(USER, "confirm", asked.buttons[0].payload)

    assert habitica.called("delete_task") == [HABIT.id]
    assert done.text == "Done: delete “Workout”."
    assert await bot_events() == [("Workout", "deleted")]


async def test_cancel_drops_the_action(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("delete_task", task_id="ab1a2b3c"), says("Confirm?"))
    asked = await harness.text_message_received(USER, "delete workout")

    cancelled = await harness.button_pressed(USER, "cancel", asked.buttons[1].payload)

    assert habitica.called("delete_task") == []
    assert cancelled.text == "Cancelled: delete “Workout”."
    async with session_scope() as db:
        from app.core.state import get_state

        assert await get_state(db, USER.id) is None


async def test_any_other_message_drops_the_pending_action(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("delete_task", task_id="ab1a2b3c"), says("Confirm?"))
    asked = await harness.text_message_received(USER, "delete workout")
    llm.queue(says("You have two tasks."))

    moved_on = await harness.text_message_received(USER, "actually, what do I have?")
    pressed_anyway = await harness.button_pressed(USER, "confirm", asked.buttons[0].payload)

    assert (
        moved_on.text
        == "(The pending action — delete “Workout” — is dropped.)\nYou have two tasks."
    )
    assert pressed_anyway.text == t("unknown_button", "en")
    assert habitica.called("delete_task") == []


async def test_a_command_also_drops_the_pending_action(harness: Harness, llm: FakeLlm) -> None:
    llm.queue(calls_tool("delete_task", task_id="ab1a2b3c"), says("Confirm?"))
    await harness.text_message_received(USER, "delete workout")

    reply = await harness.command_received(USER, "language", "en")

    assert reply.text.startswith("(The pending action — delete “Workout” — is dropped.)\n")


async def test_a_stale_button_from_an_earlier_offer_does_not_fire(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("delete_task", task_id="ab1a2b3c"), says("Confirm?"))
    first = await harness.text_message_received(USER, "delete workout")
    llm.queue(calls_tool("delete_task", task_id="d1a2b3c4"), says("Confirm the daily?"))
    await harness.text_message_received(USER, "no, delete the vacuum daily")

    stale = await harness.button_pressed(USER, "confirm", first.buttons[0].payload)

    assert stale.text == t("unknown_button", "en")
    assert habitica.called("delete_task") == []


async def test_a_confirmed_action_that_fails_is_reported(
    harness: Harness, habitica: FakeHabiticaClient, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("delete_task", task_id="ab1a2b3c"), says("Confirm?"))
    asked = await harness.text_message_received(USER, "delete workout")

    async def refuse(task_id: str) -> None:
        from app.habitica.client import HabiticaError

        raise HabiticaError(404, "NotFound", "Task not found.")

    habitica.delete_task = refuse  # type: ignore[method-assign]
    failed = await harness.button_pressed(USER, "confirm", asked.buttons[0].payload)

    assert failed.text == "Couldn't delete “Workout”: Habitica refused: Task not found."


async def test_the_button_press_is_visible_to_the_model_afterwards(
    harness: Harness, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("delete_task", task_id="ab1a2b3c"), says("Confirm?"))
    asked = await harness.text_message_received(USER, "delete workout")
    await harness.button_pressed(USER, "confirm", asked.buttons[0].payload)
    llm.queue(says("It's gone."))

    await harness.text_message_received(USER, "is it gone?")

    contents = [m.get("content") for m in llm.calls[-1].messages if m["role"] == "user"]
    assert "[pressed: confirm]" in contents


# ---- without a model ----


async def test_free_text_without_a_configured_model(
    db_path: Path, habitica: FakeHabiticaClient
) -> None:
    harness = Harness(habitica=habitica, llm=None, clock=Clock(), tz=TZ)  # type: ignore[arg-type]

    reply = await harness.text_message_received(USER, "hi")

    assert reply.text == t("llm_not_configured", "en")
    async with session_scope() as db:
        assert list(await db.scalars(select(ChatSession))) == []


def test_prefix_joins_only_when_there_is_a_line() -> None:
    assert conversation.prefix("", "text") == "text"
    assert conversation.prefix("dropped", "text") == "dropped\ntext"


# ---- /model ----


async def test_model_shows_usage_and_switches(harness: Harness, llm: FakeLlm) -> None:
    llm.queue(says("Hi."), says("Hello again."))
    await harness.text_message_received(USER, "hi")

    before = await harness.command_received(USER, "model", "")
    switched = await harness.command_received(USER, "model", "cheap")
    await harness.text_message_received(USER, "hi again")
    after = await harness.command_received(USER, "model", "")
    invalid = await harness.command_received(USER, "model", "huge")

    assert before.text.splitlines() == [
        "Models (● is yours):",
        "● strong z-ai/glm-5.3 — 1 call, $0.0001, avg 0.1 s",
        "  cheap  deepseek/deepseek-v4-flash — no calls yet",
        t("model_footer", "en"),
    ]
    assert switched.text == "Switched to cheap (deepseek/deepseek-v4-flash)."
    assert llm.calls[-1].model == "deepseek/deepseek-v4-flash"
    assert after.text.splitlines()[1:3] == [
        "  strong z-ai/glm-5.3 — 1 call, $0.0001, avg 0.1 s",
        "● cheap  deepseek/deepseek-v4-flash — 1 call, $0.0001, avg 0.1 s",
    ]
    assert invalid.text == t("model_invalid", "en")


async def test_model_counts_failed_calls(harness: Harness, llm: FakeLlm) -> None:
    llm.queue(LlmError("z-ai/glm-5.3: HTTP 500"), says("ok"))
    await harness.text_message_received(USER, "hi")
    await harness.text_message_received(USER, "hi")

    shown = await harness.command_received(USER, "model", "")

    assert "● strong z-ai/glm-5.3 — 2 calls (1 failed), $0.0001," in shown.text
