"""Sessions: the slice of conversation the model gets to see.

A message lands in the user's open session when it is within the idle limit
of its kind (quick 15 min, planning 120 min); otherwise that session is
closed and a new quick one opened. Closing writes a ≤3-line summary with the
cheap model; the next system prompts carry the last three summaries as
"Recent context". A session that outgrows the token budget has its oldest
half collapsed into a running summary, cut at a user message so a tool call
never loses its result.

The summariser is injected (`Summarizer`): this module never talks to a model
provider itself, and tests pass a stub.
"""

import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import ChatSession, Message

logger = logging.getLogger(__name__)

QUICK = "quick"
PLANNING = "planning"
KINDS = (QUICK, PLANNING)

# `messages` in the model's format → a short text. Raises on failure; callers log and go on.
Summarizer = Callable[[list[dict[str, Any]]], Awaitable[str]]

CLOSE_INSTRUCTION = (
    "Summarise this conversation between a user and their Habitica coach in at most three "
    "short lines: what was discussed, what was agreed or done (name the tasks), what was "
    "deferred. Plain text, no preamble, in the language the user wrote in."
)
COLLAPSE_INSTRUCTION = (
    "Summarise the following part of a conversation between a user and their Habitica coach "
    "so the coach can continue it without the transcript: what the user wants, what was "
    "decided or done (name the tasks and ids), what is still open. At most eight lines, "
    "plain text, no preamble."
)
TOOL_RESULT_STUB = "(result omitted; it was used in an earlier turn)"
# No tokenizer: four characters per token is close enough to keep a budget.
CHARS_PER_TOKEN = 4


def idle_limit(kind: str) -> timedelta:
    settings = get_settings()
    minutes = (
        settings.session_idle_planning_min if kind == PLANNING else settings.session_idle_quick_min
    )
    return timedelta(minutes=minutes)


async def open_session_for(db: AsyncSession, user_id: str) -> ChatSession | None:
    return await db.scalar(
        select(ChatSession)
        .where(ChatSession.user_id == user_id, ChatSession.ended_at.is_(None))
        .order_by(ChatSession.id.desc())
        .limit(1)
    )


def is_idle(chat: ChatSession, now: datetime) -> bool:
    return now - chat.last_activity_at > idle_limit(chat.kind)


async def open_session(
    db: AsyncSession, user_id: str, now: datetime, kind: str = QUICK, topic: str | None = None
) -> ChatSession:
    if kind not in KINDS:
        raise ValueError(f"unknown session kind {kind!r}")
    chat = ChatSession(
        user_id=user_id, kind=kind, topic=topic, started_at=now, last_activity_at=now
    )
    db.add(chat)
    await db.flush()
    logger.info("session %d opened (%s%s)", chat.id, kind, f": {topic}" if topic else "")
    return chat


async def ensure_session(
    db: AsyncSession, user_id: str, now: datetime, summarize: Summarizer | None
) -> ChatSession:
    """The session a new message belongs to: the open one if still fresh, else a new quick one."""
    chat = await open_session_for(db, user_id)
    if chat is not None and not is_idle(chat, now):
        return chat
    if chat is not None:
        await close_session(db, chat, now, summarize)
    return await open_session(db, user_id, now)


async def close_session(
    db: AsyncSession, chat: ChatSession, now: datetime, summarize: Summarizer | None
) -> None:
    """End the session and, when it had any conversation, keep a summary of it."""
    chat.ended_at = now
    transcript = await replay(db, chat)
    if transcript and summarize is not None:
        try:
            chat.summary = await summarize(summary_request(CLOSE_INSTRUCTION, chat, transcript))
        except Exception:
            logger.exception("session %d: summary failed; closing without one", chat.id)
    await db.flush()
    logger.info("session %d closed (%d messages)", chat.id, len(transcript))


async def append(
    db: AsyncSession,
    chat: ChatSession,
    now: datetime,
    direction: str,
    kind: str,
    text: str,
    llm: dict[str, Any],
) -> Message:
    """Log a message that belongs to the session, in both the human and the model form."""
    row = Message(
        user_id=chat.user_id,
        at=now,
        direction=direction,
        kind=kind,
        text=text,
        session_id=chat.id,
        llm=llm,
    )
    db.add(row)
    chat.last_activity_at = now
    await db.flush()
    return row


def attach(message: Message, chat: ChatSession, llm: dict[str, Any]) -> None:
    """Link an already-logged message to the session."""
    message.session_id = chat.id
    message.llm = llm
    chat.last_activity_at = max(chat.last_activity_at, message.at)


async def session_messages(db: AsyncSession, chat: ChatSession) -> list[Message]:
    """The rows the model should still see: after the collapse point, with a model form."""
    query = (
        select(Message)
        .where(Message.session_id == chat.id, Message.llm.is_not(None))
        .order_by(Message.id)
    )
    if chat.collapsed_through_id is not None:
        query = query.where(Message.id > chat.collapsed_through_id)
    return list(await db.scalars(query))


async def replay(db: AsyncSession, chat: ChatSession) -> list[dict[str, Any]]:
    """The session as prompt messages. Tool results from earlier turns become a stub."""
    rows = await session_messages(db, chat)
    messages = [dict(row.llm) for row in rows if row.llm is not None]
    return stub_old_tool_results(drop_orphaned_tool_calls(messages))


def stub_old_tool_results(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return [
        {**m, "content": TOOL_RESULT_STUB} if m.get("role") == "tool" and i < last_user else m
        for i, m in enumerate(messages)
    ]


def drop_orphaned_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A tool call whose result never got logged would be refused by the API; drop it."""
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    kept: list[dict[str, Any]] = []
    for message in messages:
        calls = message.get("tool_calls")
        if message.get("role") == "assistant" and calls:
            if all(call["id"] in answered for call in calls):
                kept.append(message)
            elif message.get("content"):
                kept.append({"role": "assistant", "content": message["content"]})
            continue
        kept.append(message)
    return kept


async def recent_summaries(db: AsyncSession, user_id: str, limit: int = 3) -> list[str]:
    """Summaries of the last closed sessions, most recent first."""
    rows = await db.scalars(
        select(ChatSession.summary)
        .where(
            ChatSession.user_id == user_id,
            ChatSession.ended_at.is_not(None),
            ChatSession.summary.is_not(None),
        )
        .order_by(ChatSession.ended_at.desc())
        .limit(limit)
    )
    return list(rows)


# ---- token budget ----


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // CHARS_PER_TOKEN


async def collapse_if_over_budget(
    db: AsyncSession, chat: ChatSession, summarize: Summarizer | None
) -> bool:
    """Fold the oldest half of the replay into `collapsed_summary`; True when it happened."""
    rows = await session_messages(db, chat)
    messages = [dict(row.llm) for row in rows if row.llm is not None]
    if summarize is None or estimate_tokens(messages) <= get_settings().session_token_budget:
        return False
    cut = cut_index(messages)
    if cut == 0:
        return False
    try:
        summary = await summarize(
            summary_request(COLLAPSE_INSTRUCTION, chat, messages[:cut], chat.collapsed_summary)
        )
    except Exception:
        logger.exception("session %d: collapse failed; keeping the transcript", chat.id)
        return False
    chat.collapsed_summary = summary
    chat.collapsed_through_id = rows[cut - 1].id
    await db.flush()
    logger.info("session %d: collapsed %d messages", chat.id, cut)
    return True


def cut_index(messages: list[dict[str, Any]]) -> int:
    """Where the oldest half ends: at the first user message from the midpoint on, so an
    assistant tool call and its results are never split."""
    midpoint = len(messages) // 2
    for i in range(midpoint, len(messages)):
        if messages[i].get("role") == "user":
            return i
    return 0


def summary_request(
    instruction: str,
    chat: ChatSession,
    messages: list[dict[str, Any]],
    earlier_summary: str | None = None,
) -> list[dict[str, Any]]:
    """The two-message prompt the summariser gets."""
    lines = []
    if chat.topic:
        lines.append(f"Topic: {chat.topic}")
    if earlier_summary:
        lines.append(f"Summary so far: {earlier_summary}")
    lines.extend(transcript_lines(messages))
    return [
        {"role": "system", "content": instruction},
        {"role": "user", "content": "\n".join(lines)},
    ]


def transcript_lines(messages: list[dict[str, Any]]) -> list[str]:
    lines = []
    for message in messages:
        role = message.get("role")
        if role == "user":
            lines.append(f"User: {message.get('content', '')}")
        elif role == "assistant":
            for call in message.get("tool_calls") or []:
                function = call["function"]
                lines.append(f"Coach called {function['name']}({function['arguments']})")
            if message.get("content"):
                lines.append(f"Coach: {message['content']}")
        elif role == "tool":
            lines.append(f"Tool result: {message.get('content', '')}")
    return lines
