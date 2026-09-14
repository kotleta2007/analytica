"""Voice notes through the core: two-pass transcription, confirm, amend, cancel."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.collector.snapshots import collect
from app.core import voice
from app.core.harness import Harness
from app.core.i18n import t
from app.core.replies import User
from app.db.engine import session_scope
from app.db.models import ConversationState, Message
from app.db.models import Transcription as TranscriptionRow
from app.speech.asr import AsrError
from tests.fakes.asr import FakeAsr, hears
from tests.fakes.habitica import FakeHabiticaClient
from tests.fakes.llm import FakeLlm, calls_tool, fails, says
from tests.task_factory import make_task

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Zurich")
AUDIO = b"OggS\x00\x02fake"
DAILY = make_task("daily", "Morning stretch", task_id="d1a2b3c4-0000-4000-8000-000000000001")


@pytest.fixture
def habitica() -> FakeHabiticaClient:
    return FakeHabiticaClient(tasks=[DAILY])


@pytest.fixture
def llm() -> FakeLlm:
    return FakeLlm()


@pytest.fixture
def asr() -> FakeAsr:
    return FakeAsr()


@pytest.fixture
async def harness(
    db_path: Path, habitica: FakeHabiticaClient, llm: FakeLlm, asr: FakeAsr
) -> Harness:
    async with session_scope() as db:
        await collect(habitica, db, USER.id, NOW - timedelta(minutes=1))
    habitica.calls.clear()
    return Harness(habitica=habitica, llm=llm, asr=asr, clock=lambda: NOW, tz=TZ)  # type: ignore[arg-type]


async def state() -> tuple[str, dict] | None:
    async with session_scope() as db:
        row = await db.get(ConversationState, USER.id)
        return None if row is None else (row.state, row.payload)


async def transcriptions() -> list[TranscriptionRow]:
    async with session_scope() as db:
        return list(await db.scalars(select(TranscriptionRow).order_by(TranscriptionRow.id)))


async def logged() -> list[tuple[str, str, str]]:
    async with session_scope() as db:
        rows = await db.scalars(select(Message).order_by(Message.id))
        return [(row.direction, row.kind, row.text) for row in rows]


def buttons_of(reply) -> list[tuple[str, str]]:
    return [(b.label, b.action) for b in reply.buttons]


# ---- the first pass and the cleanup pass ----


async def test_a_voice_note_is_transcribed_cleaned_and_shown_for_confirmation(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    asr.queue(hears("i did my morning sketch"))
    llm.queue(says("I did my morning stretch."))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == "I heard:\nI did my morning stretch."
    assert buttons_of(reply) == [
        ("✅ Correct", "confirm_transcription"),
        ("✏️ Amend", "amend_transcription"),
    ]
    assert reply.speak is False
    assert asr.calls == [(AUDIO, "audio/ogg")]

    kind, payload = await state()
    assert kind == "awaiting_confirmation"
    assert payload["raw"] == "i did my morning sketch"
    assert payload["cleaned"] == "I did my morning stretch."
    assert reply.buttons[0].payload == {"token": payload["token"]}

    (row,) = await transcriptions()
    assert (row.raw_text, row.cleaned_text, row.accepted_text) == (
        "i did my morning sketch",
        "I did my morning stretch.",
        None,
    )
    assert payload["transcription_id"] == row.id
    assert await logged() == [
        ("in", "voice", "I did my morning stretch."),
        ("out", "text", "I heard:\nI did my morning stretch."),
    ]


async def test_cleanup_uses_the_cheap_model_with_glossary_and_task_names(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    await harness.command_received(USER, "glossary", "add Vergil")
    asr.queue(hears("read virgil after the morning sketch"))
    llm.queue(says("Read Vergil after the Morning stretch."))

    await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    call = llm.calls[-1]
    assert call.model == "deepseek/deepseek-v4-flash"
    assert call.tools is None
    assert call.system.startswith(voice.CLEANUP_INSTRUCTION)
    assert "User's glossary (these spellings are right): Vergil" in call.system
    assert "Task names: Morning stretch" in call.system
    assert call.messages[1] == {"role": "user", "content": "read virgil after the morning sketch"}


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ('"I did it."', "I did it."),
        ("«I did it.»", "I did it."),
        ("", "i did it"),
        ("Sure! Here is the corrected transcript, with punctuation fixed: I did it.", "i did it"),
    ],
)
async def test_cleanup_unquotes_and_falls_back_when_the_model_chats(
    harness: Harness, asr: FakeAsr, llm: FakeLlm, answer: str, expected: str
) -> None:
    asr.queue(hears("i did it"))
    llm.queue(says(answer))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == f"I heard:\n{expected}"


async def test_cleanup_failure_keeps_the_raw_transcript(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    asr.queue(hears("raw words"))
    llm.queue(fails("model down"))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == "I heard:\nraw words"
    (row,) = await transcriptions()
    assert row.cleaned_text == "raw words"


async def test_without_a_model_the_raw_transcript_is_shown(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    harness.llm = None
    asr.queue(hears("raw words"))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == "I heard:\nraw words"
    assert llm.calls == []


async def test_asr_failure_is_reported_and_leaves_nothing_pending(
    harness: Harness, asr: FakeAsr
) -> None:
    asr.queue(AsrError("HTTP 502: bad gateway"))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == t("asr_error", "en", error="HTTP 502: bad gateway")
    assert reply.buttons == []
    assert await state() is None
    (row,) = await transcriptions()
    assert row.ok is False


async def test_silence_is_reported(harness: Harness, asr: FakeAsr) -> None:
    asr.queue(hears(""))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == t("voice_heard_nothing", "en")
    assert await state() is None


async def test_a_new_message_while_a_transcript_waits_drops_it(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    asr.queue(hears("first note"))
    llm.queue(says("First note."), says("Hello!"))
    await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    reply = await harness.text_message_received(USER, "hello")

    assert reply.text == f"{t('voice_dropped', 'en')}\nHello!"
    assert await state() is None
    # The dropped transcript was never accepted.
    (row,) = await transcriptions()
    assert row.accepted_text is None


async def test_a_command_while_a_transcript_waits_drops_it_too(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    asr.queue(hears("first note"))
    llm.queue(says("First note."))
    await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    reply = await harness.command_received(USER, "glossary", "")

    assert reply.text == f"{t('voice_dropped', 'en')}\n{t('glossary_empty', 'en')}"
    assert await state() is None


async def test_a_second_voice_note_replaces_the_first(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    asr.queue(hears("first"), hears("second"))
    llm.queue(says("First."), says("Second."))
    await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == f"{t('voice_dropped', 'en')}\nI heard:\nSecond."
    _, payload = await state()
    assert payload["cleaned"] == "Second."


async def test_a_voice_note_drops_a_parked_tool_action(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    llm.queue(calls_tool("delete_task", task_id="d1a2b3c4"), says("Delete it?"))
    await harness.text_message_received(USER, "delete morning stretch")
    asr.queue(hears("never mind"))
    llm.queue(says("Never mind."))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text.startswith("(The pending action — delete “Morning stretch” — is dropped.)\n")
    kind, _ = await state()
    assert kind == "awaiting_confirmation"
