"""The OpenRouter client against a mocked transport, and its `llm_calls` rows.

The SDK validates responses strictly, so the mocked bodies carry every field
OpenRouter always sends (`system_fingerprint` included).
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from app.db.engine import session_scope
from app.db.models import LlmCall
from app.llm import client as llm_module
from app.llm.client import LlmClient, LlmError, chat
from app.llm.models import CHEAP, STRONG, model_id, registry

USER_ID = "00000000-0000-4000-8000-000000000001"
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
MODEL = "z-ai/glm-5.3"


def completion(
    message: dict[str, Any], cost: float = 0.00123, finish_reason: str = "stop"
) -> dict[str, Any]:
    return {
        "id": "gen-1",
        "object": "chat.completion",
        "created": 1,
        "model": MODEL,
        "system_fingerprint": None,
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {"prompt_tokens": 210, "completion_tokens": 17, "total_tokens": 227, "cost": cost},
    }


class Server:
    """Records requests and answers with the queued responses."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, Any]] = []

    def client(self) -> LlmClient:
        transport = httpx.MockTransport(self.handler)
        return LlmClient("test-key", http_client=httpx.AsyncClient(transport=transport))

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


async def test_chat_sends_tools_and_parses_text() -> None:
    server = Server(httpx.Response(200, json=completion({"role": "assistant", "content": "Hi!"})))
    tools = [{"type": "function", "function": {"name": "get_stats", "parameters": {}}}]

    result = await server.client().chat([{"role": "user", "content": "hello"}], tools, MODEL)

    assert result.text == "Hi!"
    assert result.tool_calls == []
    assert (result.prompt_tokens, result.completion_tokens) == (210, 17)
    assert result.cost_usd == pytest.approx(0.00123)
    assert result.message == {"role": "assistant", "content": "Hi!"}
    sent = server.requests[0]
    assert sent["model"] == MODEL
    assert sent["messages"] == [{"role": "user", "content": "hello"}]
    assert sent["tools"] == tools
    assert "tool_choice" not in sent
    assert "max_tokens" not in sent and "reasoning" not in sent
    assert result.truncated is False


async def test_chat_can_turn_reasoning_off_and_cap_the_answer() -> None:
    body = completion({"role": "assistant", "content": "I did"}, finish_reason="length")
    server = Server(httpx.Response(200, json=body))

    result = await server.client().chat(
        [{"role": "user", "content": "i did it"}],
        None,
        MODEL,
        max_tokens=12,
        reasoning_effort="none",
    )

    sent = server.requests[0]
    assert sent["max_tokens"] == 12
    assert sent["reasoning"] == {"effort": "none"}
    assert result.truncated is True


async def test_chat_parses_tool_calls_and_forces_text_when_asked() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "score_task",
                    "arguments": '{"task_id": "abc", "direction": "up"}',
                },
            },
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "get_stats", "arguments": "{not json"},
            },
        ],
    }
    server = Server(httpx.Response(200, json=completion(message)))
    tools = [{"type": "function", "function": {"name": "score_task", "parameters": {}}}]

    result = await server.client().chat([], tools, MODEL, tool_choice="none")

    assert [(c.id, c.name) for c in result.tool_calls] == [
        ("call_1", "score_task"),
        ("call_2", "get_stats"),
    ]
    assert result.tool_calls[0].arguments == {"task_id": "abc", "direction": "up"}
    assert result.tool_calls[1].arguments == {"_unparsable_arguments": "{not json"}
    assert result.text == ""
    assert result.message["tool_calls"][0]["function"]["name"] == "score_task"
    assert "content" not in result.message
    assert server.requests[0]["tool_choice"] == "none"


async def test_http_error_becomes_llm_error() -> None:
    server = Server(
        httpx.Response(401, json={"error": {"message": "No auth credentials found"}}),
    )

    with pytest.raises(LlmError, match="HTTP 401"):
        await server.client().chat([{"role": "user", "content": "x"}], None, MODEL)


async def test_a_5xx_is_retried_briefly_then_raised() -> None:
    server = Server(*[httpx.Response(502, json={"error": {"message": "bad gateway"}})] * 5)

    with pytest.raises(LlmError, match="HTTP 502"):
        await server.client().chat([{"role": "user", "content": "x"}], None, MODEL)

    assert len(server.requests) == 3  # bounded by RETRIES, not the SDK's hour-long default


async def test_connection_error_becomes_llm_error() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = LlmClient("k", http_client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)))

    with pytest.raises(LlmError, match="connection refused"):
        await client.chat([{"role": "user", "content": "x"}], None, MODEL)


async def test_response_without_choices_is_an_error() -> None:
    body = completion({"role": "assistant", "content": "x"})
    body["choices"] = []
    server = Server(httpx.Response(200, json=body))

    with pytest.raises(LlmError, match="without choices"):
        await server.client().chat([], None, MODEL)


# ---- telemetry ----


async def rows() -> list[LlmCall]:
    async with session_scope() as session:
        return list(await session.scalars(select(LlmCall).order_by(LlmCall.id)))


async def test_successful_call_is_recorded(db_path: Path) -> None:
    server = Server(httpx.Response(200, json=completion({"role": "assistant", "content": "Hi"})))

    async with session_scope() as session:
        result = await chat(
            server.client(), session, USER_ID, NOW, [{"role": "user", "content": "hi"}], None, MODEL
        )

    assert result.text == "Hi"
    (row,) = await rows()
    assert (row.user_id, row.at, row.model, row.ok, row.error) == (USER_ID, NOW, MODEL, True, None)
    assert (row.prompt_tokens, row.completion_tokens, row.tool_calls) == (210, 17, 0)
    assert row.cost_usd == pytest.approx(0.00123)
    assert row.latency_ms >= 0


async def test_failed_call_is_recorded_and_raised(db_path: Path) -> None:
    server = Server(httpx.Response(402, json={"error": {"message": "insufficient credits"}}))

    async with session_scope() as session:
        # The harness catches the error inside its transaction, so the row is committed.
        with pytest.raises(LlmError):
            await chat(server.client(), session, USER_ID, NOW, [], None, MODEL)

    (row,) = await rows()
    assert row.ok is False
    assert row.error is not None and "402" in row.error
    assert (row.prompt_tokens, row.completion_tokens, row.cost_usd) == (0, 0, None)


# ---- registry ----


def test_registry_reads_the_configured_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_STRONG_MODEL", "vendor/big")
    monkeypatch.setenv("LLM_CHEAP_MODEL", "vendor/small")
    from app.config import get_settings

    get_settings.cache_clear()

    assert registry() == {STRONG: "vendor/big", CHEAP: "vendor/small"}
    assert model_id(CHEAP) == "vendor/small"


def test_client_from_settings_is_none_without_a_key() -> None:
    assert llm_module.client_from_settings() is None
