"""The speech client against a mock OpenRouter, and its telemetry row."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db.engine import session_scope
from app.db.models import TtsCall
from app.speech.tts import TtsClient, TtsError, speak

NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
USER_ID = "00000000-0000-4000-8000-000000000001"
MODEL = "fish-audio/s2.1-pro"
VOICE = "b347db033a6549378b48d00acb0d06cd"
MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00fake"


class Server:
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def client(self) -> TtsClient:
        transport = httpx.MockTransport(self.handler)
        return TtsClient(
            "test-key", MODEL, VOICE, http_client=httpx.AsyncClient(transport=transport)
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def audio() -> httpx.Response:
    return httpx.Response(200, content=MP3, headers={"content-type": "audio/mpeg"})


async def test_text_goes_out_with_the_voice_id_and_mp3_comes_back() -> None:
    server = Server(audio())

    result = await server.client().speak("Morning stretch scored.")

    assert result.audio == MP3
    assert result.latency_ms >= 0
    assert server.requests == [
        {
            "model": MODEL,
            "input": "Morning stretch scored.",
            "voice": VOICE,
            "response_format": "mp3",
        }
    ]


async def test_http_error_becomes_tts_error_with_the_provider_message() -> None:
    server = Server(httpx.Response(400, json={"error": {"message": "voice not found"}}))

    with pytest.raises(TtsError, match="HTTP 400: voice not found"):
        await server.client().speak("hi")


async def test_empty_audio_is_an_error() -> None:
    server = Server(httpx.Response(200, content=b""))

    with pytest.raises(TtsError, match="empty audio"):
        await server.client().speak("hi")


async def test_connection_error_becomes_tts_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = TtsClient(
        "k", MODEL, VOICE, http_client=httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )

    with pytest.raises(TtsError, match="refused"):
        await client.speak("hi")


async def rows() -> list[TtsCall]:
    async with session_scope() as session:
        return list(await session.scalars(select(TtsCall).order_by(TtsCall.id)))


async def test_successful_call_is_recorded(db_path: Path) -> None:
    server = Server(audio())

    async with session_scope() as session:
        result = await speak(server.client(), session, USER_ID, NOW, "Twelve chars")

    assert result == MP3
    (row,) = await rows()
    assert (row.provider, row.model, row.chars, row.ok) == ("openrouter", MODEL, 12, True)
    assert row.at == NOW


async def test_failed_call_is_recorded_and_raised(db_path: Path) -> None:
    server = Server(httpx.Response(503, text="busy"))

    async with session_scope() as session:
        with pytest.raises(TtsError):
            await speak(server.client(), session, USER_ID, NOW, "hi")

    (row,) = await rows()
    assert row.ok is False
    assert "HTTP 503" in (row.error or "")
