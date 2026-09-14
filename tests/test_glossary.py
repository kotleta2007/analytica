"""The ASR glossary: /glossary, the model's tool, and the bias context it feeds."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.collector.snapshots import collect
from app.core import glossary
from app.core.commands import COMMANDS, help_text
from app.core.harness import Harness
from app.core.i18n import t
from app.core.replies import User
from app.db.engine import session_scope
from app.db.models import AsrGlossaryTerm
from tests.fakes.habitica import FakeHabiticaClient
from tests.fakes.llm import FakeLlm, calls_tool, says
from tests.task_factory import make_task

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
DAILY = make_task("daily", "Morning stretch", task_id="d1a2b3c4-0000-4000-8000-000000000001")


@pytest.fixture
def habitica() -> FakeHabiticaClient:
    return FakeHabiticaClient(tasks=[DAILY])


@pytest.fixture
def llm() -> FakeLlm:
    return FakeLlm()


@pytest.fixture
async def harness(db_path: Path, habitica: FakeHabiticaClient, llm: FakeLlm) -> Harness:
    async with session_scope() as db:
        await collect(habitica, db, USER.id, NOW - timedelta(minutes=1))
    return Harness(habitica=habitica, llm=llm, clock=lambda: NOW, tz=ZoneInfo("Europe/Zurich"))  # type: ignore[arg-type]


async def stored() -> list[tuple[str, str]]:
    async with session_scope() as db:
        rows = await db.scalars(select(AsrGlossaryTerm).order_by(AsrGlossaryTerm.id))
        return [(row.term, row.added_from) for row in rows]


async def test_glossary_command_lists_adds_and_removes(harness: Harness) -> None:
    assert (await harness.command_received(USER, "glossary", "")).text == t("glossary_empty", "en")

    added = await harness.command_received(USER, "glossary", "add  Vergil ")
    assert added.text == "Added “Vergil” to your glossary."
    again = await harness.command_received(USER, "glossary", "add vergil")
    assert again.text == "“vergil” is already in your glossary."
    await harness.command_received(USER, "glossary", "add Wheelock")

    listed = await harness.command_received(USER, "glossary", "")
    assert listed.text == "Glossary:\n• Vergil\n• Wheelock"
    assert await stored() == [("Vergil", "command"), ("Wheelock", "command")]

    removed = await harness.command_received(USER, "glossary", "remove VERGIL")
    assert removed.text == "Removed “VERGIL” from your glossary."
    missing = await harness.command_received(USER, "glossary", "remove Vergil")
    assert missing.text == "“Vergil” is not in your glossary."
    assert await stored() == [("Wheelock", "command")]


async def test_glossary_command_explains_itself_on_bad_input(harness: Harness) -> None:
    assert (await harness.command_received(USER, "glossary", "add")).text == t(
        "glossary_usage", "en"
    )
    assert (await harness.command_received(USER, "glossary", "forget x")).text == t(
        "glossary_usage", "en"
    )
    too_long = await harness.command_received(USER, "glossary", "add " + "x" * 65)
    assert too_long.text == "Can't add that: a term is at most 64 characters."


async def test_the_model_can_add_a_term(harness: Harness, llm: FakeLlm) -> None:
    llm.queue(calls_tool("add_glossary_term", term="Vergil"), says("Noted: Vergil."))

    reply = await harness.text_message_received(USER, "the transcript wrote Virgil, it's Vergil")

    assert reply.text == "Noted: Vergil."
    assert await stored() == [("Vergil", "tool")]
    tool_result = llm.calls[1].messages[-1]
    assert tool_result["role"] == "tool"
    assert '"added": true' in tool_result["content"]


async def test_bias_context_carries_glossary_tasks_vocabulary_and_commands(
    harness: Harness,
) -> None:
    async with session_scope() as db:
        before = await glossary.bias_context(db, USER.id, list(COMMANDS))
    assert not before.startswith("User's glossary")
    assert "Task names: Morning stretch" in before
    assert "Habitica words: Habitica, habit, daily, todo" in before
    assert "/glossary" in before and "/stats" in before

    await harness.command_received(USER, "glossary", "add Vergil")

    async with session_scope() as db:
        after = await glossary.bias_context(db, USER.id, list(COMMANDS))
    assert after.startswith("User's glossary (these spellings are right): Vergil\n")


def test_help_lists_the_glossary_command() -> None:
    line = "/glossary [add|remove <term>] — words the voice recogniser gets wrong for you"
    assert line in help_text("en")
