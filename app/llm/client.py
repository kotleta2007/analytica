"""OpenRouter chat completions through its official Python SDK, with call telemetry.

`LlmClient.chat` is the boundary: messages in, `LlmResult` out, no database.
`chat` (the module-level function) wraps it and writes one `llm_calls` row per
attempt, successful or not, so `/model` can compare the two models on real
numbers and a broken key shows up as a row with `ok=False`.

Messages use the OpenAI wire format (`{"role": ..., "content": ...}` dicts):
that is what the API takes and what the `messages` table stores for replay.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx
from openrouter import OpenRouter, errors
from openrouter.utils import BackoffStrategy, RetryConfig
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import LlmCall

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_MS = 90_000
# The SDK's own default keeps retrying 5xx and connection errors for up to an
# hour; a chat turn cannot wait that long. Two retries, one second apart.
RETRIES = RetryConfig(
    "backoff",
    BackoffStrategy(initial_interval=1000, max_interval=1000, exponent=1.0, max_elapsed_time=2000),
    retry_connection_errors=True,
)
# Optional; OpenRouter shows it in its usage dashboard.
APP_TITLE = "Habitica Analytica"


class LlmError(RuntimeError):
    """The model call failed: network, HTTP status, or an unusable response."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LlmResult:
    text: str
    tool_calls: list[ToolCall]
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float | None
    latency_ms: int
    # The assistant message exactly as the API wants it back on the next call.
    message: dict[str, Any] = field(default_factory=dict)
    # The answer stopped at `max_tokens`: what came back is only its beginning.
    truncated: bool = False

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)


class LlmClient:
    def __init__(
        self,
        api_key: str,
        *,
        server_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._http = http_client or httpx.AsyncClient()
        self._openrouter = OpenRouter(
            api_key=api_key,
            server_url=server_url,
            x_open_router_title=APP_TITLE,
            async_client=self._http,
            retry_config=RETRIES,
            timeout_ms=REQUEST_TIMEOUT_MS,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        *,
        tool_choice: str | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> LlmResult:
        """One completion. `tool_choice="none"` forces a text answer; `reasoning_effort="none"`
        stops a reasoning model from thinking, and `max_tokens` counts its thinking too."""
        started = time.monotonic()
        logger.debug("llm %s: %d messages, %d tools", model, len(messages), len(tools or []))
        kwargs: dict[str, Any] = {}
        if tools:
            kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kwargs["reasoning"] = {"effort": reasoning_effort}
        try:
            response = await self._openrouter.chat.send_async(
                model=model, messages=messages, **kwargs
            )
        except errors.OpenRouterError as error:
            raise LlmError(f"{model}: HTTP {error.status_code}: {error.message}") from error
        except (errors.ResponseValidationError, errors.NoResponseError, httpx.HTTPError) as error:
            raise LlmError(f"{model}: {error}") from error
        latency_ms = int((time.monotonic() - started) * 1000)
        return result_from_response(response, model, latency_ms)


def result_from_response(response: Any, model: str, latency_ms: int) -> LlmResult:
    if not response.choices:
        raise LlmError(f"{model}: response without choices")
    choice = response.choices[0]
    message = choice.message
    usage = response.usage
    cost = usage.cost if usage is not None and isinstance(usage.cost, int | float) else None
    return LlmResult(
        text=message.content if isinstance(message.content, str) else "",
        tool_calls=[
            ToolCall(id=call.id, name=call.function.name, arguments=parse_arguments(call))
            for call in message.tool_calls or []
        ],
        model=response.model or model,
        prompt_tokens=usage.prompt_tokens if usage is not None else 0,
        completion_tokens=usage.completion_tokens if usage is not None else 0,
        cost_usd=float(cost) if cost is not None else None,
        latency_ms=latency_ms,
        message=replay_message(message),
        truncated=choice.finish_reason == "length",
    )


def replay_message(message: Any) -> dict[str, Any]:
    """Only what the API needs back: role, text, tool calls. Reasoning and refusal fields
    are not part of the conversation and the SDK's serializer ignores `include`."""
    replay: dict[str, Any] = {"role": "assistant"}
    if isinstance(message.content, str):
        replay["content"] = message.content
    if message.tool_calls:
        replay["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.function.name, "arguments": call.function.arguments},
            }
            for call in message.tool_calls
        ]
    return replay


def parse_arguments(call: Any) -> dict[str, Any]:
    """Models occasionally emit broken JSON; the tool layer reports it back to them."""
    raw = call.function.arguments or "{}"
    try:
        parsed = json.loads(raw)
    except ValueError:
        logger.warning("tool call %s has unparsable arguments: %.200s", call.function.name, raw)
        return {"_unparsable_arguments": raw}
    return parsed if isinstance(parsed, dict) else {"_unparsable_arguments": raw}


async def chat(
    client: LlmClient,
    session: AsyncSession,
    user_id: str,
    now: datetime,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    model: str,
    *,
    tool_choice: str | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> LlmResult:
    """`client.chat` plus one `llm_calls` row, written whether the call worked or not."""
    started = time.monotonic()
    try:
        result = await client.chat(
            messages,
            tools,
            model,
            tool_choice=tool_choice,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    except LlmError as error:
        logger.warning("llm call failed: %s", error)
        session.add(
            LlmCall(
                user_id=user_id,
                at=now,
                model=model,
                latency_ms=int((time.monotonic() - started) * 1000),
                ok=False,
                error=str(error)[:500],
            )
        )
        await session.flush()
        raise
    session.add(
        LlmCall(
            user_id=user_id,
            at=now,
            model=model,  # what was asked for; the provider may report a routed variant
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            tool_calls=len(result.tool_calls),
            ok=True,
        )
    )
    await session.flush()
    logger.info(
        "llm %s: %d+%d tokens, $%s, %dms, %d tool calls",
        result.model,
        result.prompt_tokens,
        result.completion_tokens,
        "?" if result.cost_usd is None else f"{result.cost_usd:.5f}",
        result.latency_ms,
        len(result.tool_calls),
    )
    return result


def client_from_settings() -> LlmClient | None:
    """A client when `OPENROUTER_API_KEY` is set, else None: the bot boots without it."""
    from app.config import get_settings

    settings = get_settings()
    if not settings.openrouter_api_key:
        logger.warning("OpenRouter is not configured; free text will be refused")
        return None
    return LlmClient(settings.openrouter_api_key)
