"""Voice notes through the core: two-pass transcription, confirm, amend, cancel."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app.collector.snapshots import collect
from app.core import voice
from app.core.harness import Harness
from app.core.i18n import t
from app.core.replies import ChartData, Reply, User
from app.db.engine import session_scope
from app.db.models import ConversationState, Message, TtsCall
from app.db.models import Transcription as TranscriptionRow
from app.speech.asr import AsrError
from tests.fakes.asr import FakeAsr, hears
from tests.fakes.habitica import FakeHabiticaClient
from tests.fakes.llm import FakeLlm, calls_tool, fails, says
from tests.fakes.tts import FAKE_MP3, FakeTts
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


async def test_cleanup_asks_for_no_reasoning_and_caps_the_answer(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    asr.queue(hears("x" * 20))  # longest accepted answer: 20 * 1.5 + 40 = 70 characters

    await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    (call,) = llm.calls
    assert call.reasoning_effort == "none"
    assert call.max_tokens == 140


async def test_a_cleanup_cut_off_at_the_cap_keeps_the_raw_transcript(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    asr.queue(hears("i did my morning sketch"))
    llm.queue(replace(says("I did my"), truncated=True))

    reply = await harness.voice_message_received(USER, AUDIO, "audio/ogg")

    assert reply.text == "I heard:\ni did my morning sketch"


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


# ---- confirm, amend, cancel ----


async def heard(harness: Harness, asr: FakeAsr, llm: FakeLlm, raw: str, cleaned: str):
    asr.queue(hears(raw))
    llm.queue(says(cleaned))
    return await harness.voice_message_received(USER, AUDIO, "audio/ogg")


async def test_correct_runs_the_transcript_as_a_text_message(
    harness: Harness, asr: FakeAsr, llm: FakeLlm, habitica: FakeHabiticaClient
) -> None:
    shown = await heard(harness, asr, llm, "i did my morning sketch", "I did my morning stretch.")
    llm.queue(calls_tool("score_task", task_id="d1a2b3c4", direction="up"), says("Stretched!"))

    reply = await harness.button_pressed(USER, "confirm_transcription", shown.buttons[0].payload)

    assert reply.text == "Stretched!"
    assert habitica.called("score_task") == [(DAILY.id, "up")]
    assert await state() is None
    (row,) = await transcriptions()
    assert row.accepted_text == "I did my morning stretch."
    # The model saw the transcript as a plain user message.
    turn_call = llm.calls[1]
    assert turn_call.messages[-1] == {"role": "user", "content": "I did my morning stretch."}
    rows = await logged()
    assert rows[:3] == [
        ("in", "voice", "I did my morning stretch."),
        ("out", "text", "I heard:\nI did my morning stretch."),
        ("in", "button", "confirm_transcription"),
    ]
    assert rows[-1] == ("out", "text", "Stretched!")


async def test_amend_asks_and_the_model_gets_transcript_and_correction_together(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    shown = await heard(harness, asr, llm, "i did my morning sketch", "I did my morning sketch.")

    asked = await harness.button_pressed(USER, "amend_transcription", shown.buttons[1].payload)

    assert asked.text == t("voice_amend_prompt", "en")
    assert asked.speak is False
    kind, _ = await state()
    assert kind == "awaiting_amendment"

    llm.queue(says("Got it: stretch."))
    reply = await harness.text_message_received(USER, "stretch, not sketch")

    assert reply.text == "Got it: stretch."
    assert llm.calls[-1].messages[-1] == {
        "role": "user",
        "content": 'Voice transcription: "I did my morning sketch."\n'
        'User\'s correction: "stretch, not sketch"',
    }
    assert await state() is None
    (row,) = await transcriptions()
    assert row.cleaned_text == "I did my morning sketch."
    assert row.accepted_text == "stretch, not sketch"
    # "stretch" is new and long enough; "sketch" was heard; "not" is too short.
    assert buttons_of(reply) == [("➕ Add “stretch”", "add_glossary_term")]
    assert reply.buttons[0].payload == {"term": "stretch"}


async def test_amendment_offers_glossary_terms_and_the_button_adds_one(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    await harness.command_received(USER, "glossary", "add Wheelock")
    shown = await heard(harness, asr, llm, "read virgil and wheelock", "Read Virgil and Wheelock.")
    await harness.button_pressed(USER, "amend_transcription", shown.buttons[1].payload)
    llm.queue(says("Noted."))

    reply = await harness.text_message_received(USER, "Vergil, not Virgil, and Wheelock")

    # Vergil is new; Virgil was heard; Wheelock is already in the glossary.
    assert buttons_of(reply) == [("➕ Add “Vergil”", "add_glossary_term")]

    added = await harness.button_pressed(USER, "add_glossary_term", reply.buttons[0].payload)
    assert added.text == "Added “Vergil” to your glossary."
    again = await harness.button_pressed(USER, "add_glossary_term", reply.buttons[0].payload)
    assert again.text == "“Vergil” is already in your glossary."

    # The next cleanup pass knows the spelling.
    await heard(harness, asr, llm, "virgil again", "Vergil again.")
    assert "User's glossary (these spellings are right): Vergil, Wheelock" in llm.calls[-1].system


def test_new_terms_keeps_order_dedupes_and_caps_the_offers() -> None:
    offers = voice.new_terms(
        "i read the book",
        "I read Wheelock, Vergil, Vergil, Cicero and Tacitus — the book, yes",
        known={"cicero"},
    )

    assert offers == ["Wheelock", "Vergil", "Tacitus"]


async def test_correct_after_amend_takes_the_original(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    shown = await heard(harness, asr, llm, "fine as it is", "Fine as it is.")
    await harness.button_pressed(USER, "amend_transcription", shown.buttons[1].payload)
    llm.queue(says("OK."))

    reply = await harness.button_pressed(USER, "confirm_transcription", shown.buttons[0].payload)

    assert reply.text == "OK."
    assert llm.calls[-1].messages[-1] == {"role": "user", "content": "Fine as it is."}
    assert await state() is None


async def test_a_stale_button_does_not_confirm_a_newer_transcript(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    old = await heard(harness, asr, llm, "first", "First.")
    await heard(harness, asr, llm, "second", "Second.")

    reply = await harness.button_pressed(USER, "confirm_transcription", old.buttons[0].payload)

    assert reply.text == t("unknown_button", "en")
    kind, payload = await state()
    assert (kind, payload["cleaned"]) == ("awaiting_confirmation", "Second.")
    assert len(llm.calls) == 2  # the two cleanup passes; no turn ran


async def test_buttons_without_a_pending_transcript_are_expired(harness: Harness) -> None:
    assert (await harness.button_pressed(USER, "confirm_transcription", {"token": "x"})).text == t(
        "unknown_button", "en"
    )
    assert (await harness.button_pressed(USER, "amend_transcription", {"token": "x"})).text == t(
        "unknown_button", "en"
    )
    assert (await harness.button_pressed(USER, "add_glossary_term", {})).text == t(
        "unknown_button", "en"
    )


async def test_a_command_while_awaiting_the_amendment_drops_the_transcript(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    shown = await heard(harness, asr, llm, "first", "First.")
    await harness.button_pressed(USER, "amend_transcription", shown.buttons[1].payload)

    reply = await harness.command_received(USER, "glossary", "")

    assert reply.text.startswith(t("voice_dropped", "en"))
    assert await state() is None
    (row,) = await transcriptions()
    assert row.accepted_text is None


async def test_the_flow_speaks_the_users_language(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    await harness.command_received(USER, "language", "ru")

    shown = await heard(harness, asr, llm, "я сделал зарядку", "Я сделал зарядку.")

    assert shown.text == "Я услышал:\nЯ сделал зарядку."
    assert buttons_of(shown) == [
        ("✅ Верно", "confirm_transcription"),
        ("✏️ Исправить", "amend_transcription"),
    ]


# ---- voice out ----


async def test_tts_is_off_by_default_even_after_voice_input(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    tts = FakeTts()
    harness.tts = tts  # type: ignore[assignment]
    shown = await heard(harness, asr, llm, "hello there", "Hello there.")
    llm.queue(says("Hi!"))

    reply = await harness.button_pressed(USER, "confirm_transcription", shown.buttons[0].payload)

    assert shown.voice is None and reply.voice is None
    assert tts.calls == []
    assert (await harness.command_received(USER, "tts", "")).text == t("tts_off", "en")


async def test_tts_on_speaks_text_and_voice_turns_but_never_the_transcript_echo(
    harness: Harness, asr: FakeAsr, llm: FakeLlm
) -> None:
    tts = FakeTts()
    harness.tts = tts  # type: ignore[assignment]
    switched = await harness.command_received(USER, "tts", "on")
    assert switched.text == t("tts_on", "en")
    assert switched.voice == FAKE_MP3  # the confirmation itself is spoken

    llm.queue(says("**Stretched!** Nice *work*."))
    typed = await harness.text_message_received(USER, "did my stretch")
    assert typed.voice == FAKE_MP3
    assert tts.calls[-1] == "Stretched! Nice work."

    shown = await heard(harness, asr, llm, "and the vacuum", "And the vacuum.")
    assert shown.voice is None  # the user's own words are not read back

    llm.queue(says("Vacuumed."))
    spoken = await harness.button_pressed(USER, "confirm_transcription", shown.buttons[0].payload)
    assert spoken.voice == FAKE_MP3
    assert tts.calls[-1] == "Vacuumed."

    async with session_scope() as db:
        rows = list(await db.scalars(select(TtsCall).order_by(TtsCall.id)))
    assert [(row.chars, row.ok) for row in rows] == [
        (len(t("tts_on", "en")), True),
        (len("Stretched! Nice work."), True),
        (len("Vacuumed."), True),
    ]


async def test_long_replies_and_charts_are_text_only(harness: Harness, llm: FakeLlm) -> None:
    tts = FakeTts()
    harness.tts = tts  # type: ignore[assignment]
    await harness.command_received(USER, "tts", "on")
    llm.queue(says("word " * 130))

    reply = await harness.text_message_received(USER, "tell me everything")

    assert len(reply.text) > voice.TTS_MAX_CHARS
    assert reply.voice is None
    assert voice.wants_voice("on", Reply("short", chart=ChartData("t", [], {}))) is False
    assert voice.wants_voice("on", Reply("")) is False
    assert voice.wants_voice("on", Reply("short")) is True


async def test_a_failed_synthesis_still_sends_the_text(harness: Harness, llm: FakeLlm) -> None:
    harness.tts = FakeTts(fail=True)  # type: ignore[assignment]
    await harness.command_received(USER, "tts", "on")
    llm.queue(says("Fine."))

    reply = await harness.text_message_received(USER, "hi")

    assert reply.text == "Fine."
    assert reply.voice is None
    async with session_scope() as db:
        rows = list(await db.scalars(select(TtsCall)))
    assert [row.ok for row in rows] == [False, False]


async def test_tts_on_is_refused_without_a_synthesiser(harness: Harness) -> None:
    reply = await harness.command_received(USER, "tts", "on")

    assert reply.text == t("tts_not_configured", "en")
    assert (await harness.command_received(USER, "tts", "")).text == t("tts_off", "en")
    assert (await harness.command_received(USER, "tts", "loud")).text == t("tts_usage", "en")


@pytest.mark.parametrize(
    ("markdown", "spoken"),
    [
        ("**Done:** *Vacuum the floors*.", "Done: Vacuum the floors."),
        ("# Today\n- one\n- two", "Today one two"),
        ("see [the docs](https://x.y) and `code`", "see the docs and code"),
        ("snake_case stays, 2*3 stays", "snake_case stays, 2*3 stays"),
    ],
)
def test_spoken_text_drops_the_markdown(markdown: str, spoken: str) -> None:
    assert voice.spoken_text(markdown) == spoken
