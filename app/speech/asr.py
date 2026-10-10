"""Speech → text through OpenRouter's transcription endpoint, with call telemetry.

`AsrClient.transcribe` is the boundary: audio bytes in, `Transcription` out,
no database. `transcribe` (the module-level function) wraps it and writes one
`transcriptions` row per attempt, successful or not; the two-pass flow later
fills in the cleaned and accepted texts on that row.

The endpoint takes base64 audio plus a format name and answers with the text
and its cost; it has no field for a vocabulary hint, so biasing the
recognition towards task names and the glossary happens in the cleanup pass
(`core/voice.py`), not here. Language is auto-detected and not reported back.
"""

import base64
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Transcription as TranscriptionRow

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1"
PROVIDER = "openrouter"
REQUEST_TIMEOUT_S = 60.0
# Telegram voice notes are Ogg/Opus; the rest is what the CLI's `/voice file` may hand over.
FORMATS = {
    "audio/ogg": "ogg",
    "audio/opus": "ogg",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/x-m4a": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/webm": "webm",
    "audio/flac": "flac",
    "audio/aac": "aac",
}


class AsrError(RuntimeError):
    """The transcription failed: unsupported audio, network, HTTP status, or a bad response."""


@dataclass(frozen=True)
class Transcription:
    text: str
    audio_seconds: float | None
    cost_usd: float | None
    latency_ms: int


def audio_format(mime_type: str) -> str:
    """`"audio/ogg; codecs=opus"` → `"ogg"`; raises AsrError for a type the endpoint can't take."""
    base = mime_type.split(";", 1)[0].strip().lower()
    try:
        return FORMATS[base]
    except KeyError:
        raise AsrError(f"unsupported audio type {mime_type!r}") from None


class AsrClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = OPENROUTER_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._url = f"{base_url.rstrip('/')}/audio/transcriptions"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http_client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def transcribe(self, audio: bytes, mime_type: str) -> Transcription:
        fmt = audio_format(mime_type)
        payload = {
            "model": self.model,
            "input_audio": {"data": base64.b64encode(audio).decode("ascii"), "format": fmt},
        }
        logger.debug("asr %s: %d bytes of %s", self.model, len(audio), fmt)
        started = time.monotonic()
        try:
            response = await self._http.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as error:
            raise AsrError(f"{self.model}: {error}") from error
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            raise AsrError(f"{self.model}: HTTP {response.status_code}: {error_text(response)}")
        try:
            body = response.json()
        except ValueError as error:
            raise AsrError(f"{self.model}: response is not JSON") from error
        return transcription_from_json(body, latency_ms, self.model)


def transcription_from_json(body: Any, latency_ms: int, model: str) -> Transcription:
    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise AsrError(f"{model}: response without text")
    usage = body.get("usage") or {}
    return Transcription(
        text=text.strip(),
        audio_seconds=number_or_none(usage.get("seconds")),
        cost_usd=number_or_none(usage.get("cost")),
        latency_ms=latency_ms,
    )


def number_or_none(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def error_text(response: httpx.Response) -> str:
    """OpenRouter's error bodies are JSON with a message inside; fall back to the raw text."""
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:200]
    return response.text[:200]


async def transcribe(
    client: AsrClient,
    session: AsyncSession,
    user_id: str,
    now: datetime,
    audio: bytes,
    mime_type: str,
) -> tuple[Transcription, TranscriptionRow]:
    """`client.transcribe` plus one `transcriptions` row, written whether the call worked or not.

    The row comes back too: the voice flow adds the cleaned and accepted texts to it later.
    Commits the caller's session first, as `llm.client.chat` does: no write lock across the call.
    """
    await session.commit()
    started = time.monotonic()
    try:
        result = await client.transcribe(audio, mime_type)
    except AsrError as error:
        logger.warning("asr call failed: %s", error)
        session.add(
            TranscriptionRow(
                user_id=user_id,
                at=now,
                provider=PROVIDER,
                model=client.model,
                raw_text="",
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=False,
                error=str(error)[:500],
            )
        )
        await session.flush()
        raise
    row = TranscriptionRow(
        user_id=user_id,
        at=now,
        provider=PROVIDER,
        model=client.model,
        raw_text=result.text,
        audio_seconds=result.audio_seconds,
        cost_usd=result.cost_usd,
        latency_ms=result.latency_ms,
        ok=True,
    )
    session.add(row)
    await session.flush()
    logger.info(
        "asr %s: %ss of audio, $%s, %dms, %d chars",
        client.model,
        "?" if result.audio_seconds is None else f"{result.audio_seconds:g}",
        "?" if result.cost_usd is None else f"{result.cost_usd:.5f}",
        result.latency_ms,
        len(result.text),
    )
    return result, row


def client_from_settings() -> AsrClient | None:
    """A client when `OPENROUTER_API_KEY` is set, else None: voice notes are then declined."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.openrouter_api_key:
        logger.warning("OpenRouter is not configured; voice messages will be declined")
        return None
    return AsrClient(settings.openrouter_api_key, settings.asr_model)
