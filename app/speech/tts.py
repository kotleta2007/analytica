"""Text → speech through OpenRouter's speech endpoint, with call telemetry.

`TtsClient.speak` is the boundary: text in, mp3 bytes out, no database.
`speak` (the module-level function) wraps it and writes one `tts_calls` row
per attempt, successful or not.

One voice for every language: `TTS_VOICE` is a Fish Audio reference id and
goes in the request's `voice` field. The endpoint offers `mp3` or `pcm`;
Telegram takes mp3 as a voice note, so mp3 it is, end to end.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import TtsCall
from app.speech.asr import OPENROUTER_URL, PROVIDER, error_text

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 60.0
FORMAT = "mp3"
MIME_TYPE = "audio/mpeg"


class TtsError(RuntimeError):
    """The synthesis failed: network, HTTP status, or an empty response."""


@dataclass(frozen=True)
class Speech:
    audio: bytes  # mp3
    latency_ms: int


class TtsClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        *,
        base_url: str = OPENROUTER_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self.voice = voice
        self._url = f"{base_url.rstrip('/')}/audio/speech"
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._http = http_client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_S)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def speak(self, text: str) -> Speech:
        payload = {
            "model": self.model,
            "input": text,
            "voice": self.voice,
            "response_format": FORMAT,
        }
        logger.debug("tts %s: %d chars", self.model, len(text))
        started = time.monotonic()
        try:
            response = await self._http.post(self._url, json=payload, headers=self._headers)
        except httpx.HTTPError as error:
            raise TtsError(f"{self.model}: {error}") from error
        latency_ms = int((time.monotonic() - started) * 1000)
        if response.status_code != 200:
            raise TtsError(f"{self.model}: HTTP {response.status_code}: {error_text(response)}")
        if not response.content:
            raise TtsError(f"{self.model}: empty audio")
        return Speech(audio=response.content, latency_ms=latency_ms)


async def speak(
    client: TtsClient, session: AsyncSession, user_id: str, now: datetime, text: str
) -> bytes:
    """`client.speak` plus one `tts_calls` row, written whether the call worked or not.

    Commits the caller's session first, as `llm.client.chat` does: no write lock across the call.
    """
    await session.commit()
    started = time.monotonic()
    try:
        result = await client.speak(text)
    except TtsError as error:
        logger.warning("tts call failed: %s", error)
        session.add(
            TtsCall(
                user_id=user_id,
                at=now,
                provider=PROVIDER,
                model=client.model,
                chars=len(text),
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=False,
                error=str(error)[:500],
            )
        )
        await session.flush()
        raise
    session.add(
        TtsCall(
            user_id=user_id,
            at=now,
            provider=PROVIDER,
            model=client.model,
            chars=len(text),
            latency_ms=result.latency_ms,
            ok=True,
        )
    )
    await session.flush()
    logger.info(
        "tts %s: %d chars → %d bytes, %dms",
        client.model,
        len(text),
        len(result.audio),
        result.latency_ms,
    )
    return result.audio


def client_from_settings() -> TtsClient | None:
    """A client when `OPENROUTER_API_KEY` is set, else None: `/tts on` is then refused."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.openrouter_api_key:
        return None
    return TtsClient(settings.openrouter_api_key, settings.tts_model, settings.tts_voice)
