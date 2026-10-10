"""A voice note: transcribe, clean up, show, and only then act on it.

    voice note → ASR (raw) → cheap model (cleaned) → "I heard: …"  ✅ Correct  ✏️ Amend
    ✅ Correct → the cleaned text runs as a text message
    ✏️ Amend   → the next text message is the correction; the model gets both

The recogniser has no vocabulary hint, so the cleanup pass is where the
glossary and the task names correct what was misheard. Both texts are kept
on the `transcriptions` row, and the accepted one is added when the user
confirms or amends, so the two passes can be measured against each other.

Any other message while a transcript is waiting drops it; the harness says so
in one line and handles the new message.
"""

import logging
import re
import uuid
from typing import Any

from app.core import commands, conversation, glossary
from app.core.i18n import t
from app.core.replies import Button, Reply
from app.core.state import clear_state, get_state, set_state
from app.core.turn import Turn
from app.db.models import Transcription as TranscriptionRow
from app.llm.client import LlmError, chat
from app.llm.models import CHEAP, model_id
from app.speech.asr import AsrClient, AsrError, transcribe
from app.speech.tts import TtsClient, TtsError, speak

logger = logging.getLogger(__name__)

AWAITING_CONFIRMATION = "awaiting_confirmation"
AWAITING_AMENDMENT = "awaiting_amendment"
CONFIRM_ACTION = "confirm_transcription"
AMEND_ACTION = "amend_transcription"
ADD_TERM_ACTION = "add_glossary_term"

CLEANUP_INSTRUCTION = (
    "You correct speech-recognition output. Fix punctuation and capitalisation, and fix words "
    "the recogniser misheard when the context below shows the intended term (a task name, a "
    "glossary entry, a Habitica word, a command). Do not change the meaning, do not add or "
    "remove anything, do not translate, do not answer the text. Reply with the corrected text "
    "only, on its own, without quotes or commentary."
)
# A cleanup that comes back much longer than the transcript is commentary, not a correction.
CLEANUP_MAX_GROWTH = 1.5
CLEANUP_MAX_EXTRA_CHARS = 40
# Fixing punctuation needs no thinking: with reasoning on, the cheap model once spent 20k
# tokens and 126 s on a 30-character note (2026-10-09).
CLEANUP_REASONING = "none"
# Words offered for the glossary after an amendment: long enough to be a term, at most this many.
MIN_TERM_LENGTH = 4
MAX_TERM_OFFERS = 3
WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*")


async def receive(turn: Turn, asr: AsrClient | None, audio: bytes, mime_type: str) -> Reply:
    """What `voice_message_received` does: transcribe, clean, ask to confirm."""
    dropped = await drop_pending(turn) or await conversation.drop_pending(turn)
    if asr is None:
        return Reply(conversation.prefix(dropped, t("voice_not_configured", turn.language)))
    try:
        result, row = await transcribe(asr, turn.session, turn.user.id, turn.now, audio, mime_type)
    except AsrError as error:
        return Reply(conversation.prefix(dropped, t("asr_error", turn.language, error=error)))
    if not result.text:
        return Reply(conversation.prefix(dropped, t("voice_heard_nothing", turn.language)))
    cleaned = await cleanup(turn, result.text)
    row.cleaned_text = cleaned
    if turn.inbound is not None:
        turn.inbound.text = cleaned  # the chat log shows what was said, not the byte count
    token = uuid.uuid4().hex[:12]
    await set_state(
        turn.session,
        turn.user.id,
        AWAITING_CONFIRMATION,
        {"transcription_id": row.id, "raw": result.text, "cleaned": cleaned, "token": token},
        turn.now,
    )
    return Reply(
        conversation.prefix(dropped, t("voice_heard", turn.language, text=cleaned)),
        buttons=[
            Button(t("voice_correct", turn.language), CONFIRM_ACTION, {"token": token}),
            Button(t("voice_amend", turn.language), AMEND_ACTION, {"token": token}),
        ],
        speak=False,
    )


async def cleanup(turn: Turn, raw: str) -> str:
    """The cheap model's pass over the transcript; the raw text without a model, or on failure."""
    if turn.llm is None:
        return raw
    context = await glossary.bias_context(turn.session, turn.user.id, list(commands.COMMANDS))
    messages = [
        {"role": "system", "content": f"{CLEANUP_INSTRUCTION}\n\n{context}"},
        {"role": "user", "content": raw},
    ]
    longest = cleanup_limit(raw)
    try:
        result = await chat(
            turn.llm,
            turn.session,
            turn.user.id,
            turn.now,
            messages,
            None,
            model_id(CHEAP),
            # Room for the longest acceptable answer even where a byte-level tokenizer splits
            # a Cyrillic letter or an emoji into two tokens; anything longer is refused anyway.
            max_tokens=2 * longest,
            reasoning_effort=CLEANUP_REASONING,
        )
    except LlmError as error:
        logger.warning("transcript cleanup failed, keeping the raw text: %s", error)
        return raw
    if result.truncated:
        logger.warning("transcript cleanup hit its token cap, keeping the raw text")
        return raw
    cleaned = unquote(result.text.strip())
    if not cleaned or len(cleaned) > longest:
        logger.warning("transcript cleanup returned something else, keeping the raw text")
        return raw
    return cleaned


def cleanup_limit(raw: str) -> int:
    """The longest cleanup we accept, in characters."""
    return int(len(raw) * CLEANUP_MAX_GROWTH + CLEANUP_MAX_EXTRA_CHARS)


def unquote(text: str) -> str:
    """Models like to wrap "the text only" in quotes anyway."""
    if len(text) >= 2 and text[0] in "\"'«“" and text[-1] in "\"'»”":
        return text[1:-1].strip()
    return text


# ---- the buttons and the amendment ----


async def confirm(turn: Turn, payload: dict[str, Any]) -> Reply:
    """✅ Correct: the cleaned transcript runs exactly as a text message would."""
    waiting = await matching_pending(turn, payload)
    if waiting is None:
        return Reply(t("unknown_button", turn.language))
    await clear_state(turn.session, turn.user.id)
    await accept(turn, waiting, waiting["cleaned"])
    return await conversation.answer(turn, waiting["cleaned"])


async def amend(turn: Turn, payload: dict[str, Any]) -> Reply:
    """✏️ Amend: the next text message is the correction."""
    waiting = await matching_pending(turn, payload)
    if waiting is None:
        return Reply(t("unknown_button", turn.language))
    await set_state(turn.session, turn.user.id, AWAITING_AMENDMENT, waiting, turn.now)
    return Reply(t("voice_amend_prompt", turn.language), speak=False)


async def amended(turn: Turn, correction: str) -> Reply:
    """The text that follows ✏️ Amend: the model gets the transcript and the correction together,
    and words that appear only in the correction are offered for the glossary."""
    state = await pending(turn)
    if state is None or state[0] != AWAITING_AMENDMENT:
        raise RuntimeError("amended() called without a transcript awaiting amendment")
    waiting = state[1]
    await clear_state(turn.session, turn.user.id)
    await accept(turn, waiting, correction)
    reply = await conversation.answer(turn, combined_message(waiting["cleaned"], correction))
    known = {term.lower() for term in await glossary.list_terms(turn.session, turn.user.id)}
    for term in new_terms(waiting["cleaned"], correction, known):
        reply.buttons.append(
            Button(t("glossary_offer", turn.language, term=term), ADD_TERM_ACTION, {"term": term})
        )
    return reply


def combined_message(transcript: str, correction: str) -> str:
    return f'Voice transcription: "{transcript}"\nUser\'s correction: "{correction}"'


def new_terms(transcript: str, correction: str, known: set[str]) -> list[str]:
    """Words only the correction has, in its order: the spellings the recogniser missed."""
    heard = {word.lower() for word in WORD.findall(transcript)}
    offers: list[str] = []
    for word in WORD.findall(correction):
        key = word.lower()
        if len(word) < MIN_TERM_LENGTH or key in heard or key in known:
            continue
        if key not in (offer.lower() for offer in offers):
            offers.append(word)
    return offers[:MAX_TERM_OFFERS]


async def add_term(turn: Turn, payload: dict[str, Any]) -> Reply:
    """➕ Add “word”: the button after an amendment. Stateless, so it works any time."""
    term = payload.get("term")
    if not isinstance(term, str) or not term.strip():
        return Reply(t("unknown_button", turn.language))
    added = await glossary.add_term(
        turn.session, turn.user.id, term, glossary.FROM_AMENDMENT, turn.now
    )
    key = "glossary_added" if added else "glossary_exists"
    return Reply(t(key, turn.language, term=glossary.normalise(term)))


async def accept(turn: Turn, waiting: dict[str, Any], text: str) -> None:
    row = await turn.session.get(TranscriptionRow, waiting["transcription_id"])
    if row is None:
        logger.warning("transcription %s is gone; accepted text lost", waiting["transcription_id"])
        return
    row.accepted_text = text
    await turn.session.flush()


# ---- voice out ----

TTS_ON = "on"
TTS_OFF = "off"
TTS_MAX_CHARS = 600
MARKDOWN_MARKS = re.compile(r"\*\*|__|`+|~~|^#{1,6}\s+|^\s*[-*]\s+", re.MULTILINE)
EMPHASIS = re.compile(r"(?<!\w)[*_](?!\s)(.+?)(?<!\s)[*_](?!\w)")
LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


def wants_voice(settings_tts: str, reply: Reply) -> bool:
    """The setting decides; long replies, charts and echoes of the user's own words stay text."""
    return (
        settings_tts == TTS_ON
        and reply.speak
        and reply.chart is None
        and 0 < len(reply.text) <= TTS_MAX_CHARS
    )


async def voice_for(turn: Turn, tts: TtsClient | None, reply: Reply) -> bytes | None:
    """The mp3 for a reply when the policy says so; None on failure, the text still goes out."""
    if tts is None or not wants_voice(turn.settings.tts, reply):
        return None
    try:
        return await speak(tts, turn.session, turn.user.id, turn.now, spoken_text(reply.text))
    except TtsError:
        return None


def spoken_text(text: str) -> str:
    """The model's Markdown, read aloud: marks off, link text kept."""
    text = LINK.sub(r"\1", text)
    text = MARKDOWN_MARKS.sub("", text)
    text = EMPHASIS.sub(r"\1", text)
    return " ".join(text.split())


# ---- the pending transcript ----


async def pending(turn: Turn) -> tuple[str, dict[str, Any]] | None:
    """`(state, payload)` when a transcript is waiting to be confirmed or amended."""
    state = await get_state(turn.session, turn.user.id)
    if state is None or state.state not in (AWAITING_CONFIRMATION, AWAITING_AMENDMENT):
        return None
    return state.state, state.payload


async def awaiting_amendment(turn: Turn) -> bool:
    state = await pending(turn)
    return state is not None and state[0] == AWAITING_AMENDMENT


async def drop_pending(turn: Turn) -> str:
    """Any message other than the buttons drops a waiting transcript; returns the line saying so."""
    if await pending(turn) is None:
        return ""
    await clear_state(turn.session, turn.user.id)
    return t("voice_dropped", turn.language)


async def matching_pending(turn: Turn, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The waiting transcript, only if this button was made for it. Either button is fine in
    either state: Amend then Correct means "the original was right after all"."""
    state = await pending(turn)
    if state is None or payload.get("token") != state[1].get("token"):
        return None
    return state[1]
