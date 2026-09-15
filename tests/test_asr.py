"""The transcription client against a mock OpenRouter, and its telemetry row."""

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db.engine import session_scope
from app.db.models import Transcription as TranscriptionRow
from app.speech.asr import AsrClient, AsrError, audio_format, transcribe

NOW = datetime(2026, 9, 14, 18, 0, tzinfo=UTC)
USER_ID = "00000000-0000-4000-8000-000000000001"
MODEL = "qwen/qwen3-asr-flash-2026-02-10"
AUDIO = b"OggS\x00\x02fake-opus-bytes"


class Server:
    """Records requests and answers with the queued responses."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[httpx.Headers] = []

    def client(self) -> AsrClient:
        transport = httpx.MockTransport(self.handler)
        return AsrClient("test-key", MODEL, http_client=httpx.AsyncClient(transport=transport))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        self.headers.append(request.headers)
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def ok(text: str, seconds: float = 3.0, cost: float = 0.000105) -> httpx.Response:
    return httpx.Response(200, json={"text": text, "usage": {"seconds": seconds, "cost": cost}})


async def test_audio_is_sent_base64_with_its_format_and_the_text_comes_back() -> None:
    server = Server(ok(" I did my morning stretch. "))

    result = await server.client().transcribe(AUDIO, "audio/ogg")

    assert result.text == "I did my morning stretch."
    assert result.audio_seconds == 3.0
    assert result.cost_usd == 0.000105
    assert result.latency_ms >= 0
    request = server.requests[0]
    assert request["model"] == MODEL
    assert request["input_audio"] == {
        "data": base64.b64encode(AUDIO).decode(),
        "format": "ogg",
    }
    assert server.headers[0]["authorization"] == "Bearer test-key"


@pytest.mark.parametrize(
    ("mime", "fmt"),
    [
        ("audio/ogg", "ogg"),
        ("audio/ogg; codecs=opus", "ogg"),
        ("AUDIO/MPEG", "mp3"),
        ("audio/x-m4a", "m4a"),
        ("audio/wav", "wav"),
    ],
)
def test_mime_types_map_to_the_endpoint_formats(mime: str, fmt: str) -> None:
    assert audio_format(mime) == fmt


async def test_an_unsupported_audio_type_is_refused_before_any_request() -> None:
    server = Server(ok("never"))

    with pytest.raises(AsrError, match="unsupported audio type 'image/png'"):
        await server.client().transcribe(AUDIO, "image/png")

    assert server.requests == []


async def test_http_error_becomes_asr_error_with_the_provider_message() -> None:
    server = Server(httpx.Response(402, json={"error": {"message": "Insufficient credits"}}))

    with pytest.raises(AsrError, match="HTTP 402: Insufficient credits"):
        await server.client().transcribe(AUDIO, "audio/ogg")


async def test_connection_error_becomes_asr_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = AsrClient(
        "k", MODEL, http_client=httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    )

    with pytest.raises(AsrError, match="refused"):
        await client.transcribe(AUDIO, "audio/ogg")


async def test_response_without_text_is_an_error() -> None:
    server = Server(httpx.Response(200, json={"usage": {"seconds": 1}}))

    with pytest.raises(AsrError, match="without text"):
        await server.client().transcribe(AUDIO, "audio/ogg")


async def rows() -> list[TranscriptionRow]:
    async with session_scope() as session:
        return list(await session.scalars(select(TranscriptionRow).order_by(TranscriptionRow.id)))


async def test_successful_call_is_recorded(db_path: Path) -> None:
    server = Server(ok("Morning stretch done"))

    async with session_scope() as session:
        result, row = await transcribe(server.client(), session, USER_ID, NOW, AUDIO, "audio/ogg")
        assert row.id is not None

    (stored,) = await rows()
    assert result.text == "Morning stretch done"
    assert (stored.provider, stored.model, stored.raw_text) == ("openrouter", MODEL, result.text)
    assert (stored.audio_seconds, stored.cost_usd, stored.ok) == (3.0, 0.000105, True)
    assert stored.cleaned_text is None and stored.accepted_text is None
    assert stored.at == NOW


async def test_failed_call_is_recorded_and_raised(db_path: Path) -> None:
    server = Server(httpx.Response(500, text="upstream down"))

    async with session_scope() as session:
        with pytest.raises(AsrError):
            await transcribe(server.client(), session, USER_ID, NOW, AUDIO, "audio/ogg")

    (stored,) = await rows()
    assert stored.ok is False
    assert stored.raw_text == ""
    assert "HTTP 500" in (stored.error or "")
