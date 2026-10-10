"""A scripted stand-in for LlmClient with the same `chat` signature.

Tests queue what the model "says" — text, tool calls, or a failure — and read
`calls` to see what the code under test sent: messages, tools, model.
"""

from collections import deque
from dataclasses import dataclass
from typing import Any

from app.llm.client import LlmError, LlmResult, ToolCall


@dataclass
class FakeCall:
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None
    model: str
    tool_choice: str | None
    max_tokens: int | None = None
    reasoning_effort: str | None = None

    @property
    def system(self) -> str:
        return self.messages[0]["content"] if self.messages[0]["role"] == "system" else ""

    @property
    def tool_names(self) -> list[str]:
        return [tool["function"]["name"] for tool in self.tools or []]


def says(text: str) -> LlmResult:
    return LlmResult(
        text=text,
        tool_calls=[],
        model="fake",
        prompt_tokens=100,
        completion_tokens=20,
        cost_usd=0.0001,
        latency_ms=50,
        message={"role": "assistant", "content": text},
    )


def calls_tool(name: str, call_id: str = "call_1", **arguments: Any) -> LlmResult:
    return calls_tools([ToolCall(id=call_id, name=name, arguments=arguments)])


def calls_tools(tool_calls: list[ToolCall]) -> LlmResult:
    return LlmResult(
        text="",
        tool_calls=tool_calls,
        model="fake",
        prompt_tokens=100,
        completion_tokens=30,
        cost_usd=0.0002,
        latency_ms=60,
        message={
            "role": "assistant",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": _json(call.arguments)},
                }
                for call in tool_calls
            ],
        },
    )


def fails(reason: str = "boom") -> LlmError:
    return LlmError(reason)


def _json(value: dict[str, Any]) -> str:
    import json

    return json.dumps(value)


class FakeLlm:
    def __init__(self, *script: LlmResult | LlmError) -> None:
        self.script: deque[LlmResult | LlmError] = deque(script)
        self.calls: list[FakeCall] = []

    def queue(self, *script: LlmResult | LlmError) -> None:
        self.script.extend(script)

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
        self.calls.append(
            FakeCall(list(messages), tools, model, tool_choice, max_tokens, reasoning_effort)
        )
        if not self.script:
            return says("OK")
        step = self.script.popleft()
        if isinstance(step, LlmError):
            raise step
        if tool_choice == "none" and step.tool_calls:
            raise AssertionError("scripted tool calls where the code forced a text answer")
        return step

    async def aclose(self) -> None:
        pass
