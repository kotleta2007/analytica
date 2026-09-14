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
    try:
        result = await chat(
            turn.llm, turn.session, turn.user.id, turn.now, messages, None, model_id(CHEAP)
        )
    except LlmError as error:
        logger.warning("transcript cleanup failed, keeping the raw text: %s", error)
        return raw
    cleaned = unquote(result.text.strip())
    if not cleaned or len(cleaned) > len(raw) * CLEANUP_MAX_GROWTH + CLEANUP_MAX_EXTRA_CHARS:
        logger.warning("transcript cleanup returned something else, keeping the raw text")
        return raw
    return cleaned


def unquote(text: str) -> str:
    """Models like to wrap "the text only" in quotes anyway."""
    if len(text) >= 2 and text[0] in "\"'«“" and text[-1] in "\"'»”":
        return text[1:-1].strip()
    return text


# ---- the pending transcript ----


async def pending(turn: Turn) -> tuple[str, dict[str, Any]] | None:
    """`(state, payload)` when a transcript is waiting to be confirmed or amended."""
    state = await get_state(turn.session, turn.user.id)
    if state is None or state.state not in (AWAITING_CONFIRMATION, AWAITING_AMENDMENT):
        return None
    return state.state, state.payload


async def drop_pending(turn: Turn) -> str:
    """Any message other than the buttons drops a waiting transcript; returns the line saying so."""
    if await pending(turn) is None:
        return ""
    await clear_state(turn.session, turn.user.id)
    return t("voice_dropped", turn.language)


async def transcription_row(turn: Turn, payload: dict[str, Any]) -> TranscriptionRow | None:
    return await turn.session.get(TranscriptionRow, payload["transcription_id"])
