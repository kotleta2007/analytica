"""A free-text turn: session → system prompt → tool loop → reply.

The model gets the session's replay behind a fresh system prompt and may call
tools for up to `MAX_TOOL_ROUNDS` rounds; after that it is forced to answer
in text. Every model message and tool result is logged into the session so
the next turn can replay it. A round that changed tasks ends with a
collection round whose events carry `source=bot`.

The confirm buttons live here too: a destructive tool parks its action in
`conversation_state`, and only ✅ Do it runs it. Everything parked in one turn
waits behind a single pair of buttons and runs in order, with a collection round
after each action, so the next one's checks (mana) see what the last one did; the
first failure stops the rest. ❌ Cancel, or any other message, drops them all.
"""

import json
import logging
from typing import Any

import httpx

from app.collector.snapshots import collect, collect_if_stale
from app.core import sessions
from app.core.i18n import t
from app.core.replies import Button, Reply
from app.core.skills import AvailableSkill, available_skills
from app.core.state import clear_state
from app.core.tasks import load_known_tasks, open_tasks
from app.core.turn import Turn, summarizer
from app.habitica.client import HabiticaError
from app.habitica.types import UserStats, user_stats_from_json
from app.llm import tools
from app.llm.client import LlmClient, LlmError, LlmResult, ToolCall, chat
from app.llm.models import model_id
from app.llm.prompts import build_system_prompt

logger = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 6
BOT_SOURCE = "bot"


async def answer(turn: Turn, text: str) -> Reply:
    """What `text_message_received` does once the adapter has handed over real text."""
    dropped = await drop_pending(turn)
    if turn.llm is None:
        return Reply(prefix(dropped, t("llm_not_configured", turn.language)))
    chat_session = await sessions.ensure_session(
        turn.session, turn.user.id, turn.now, summarizer(turn)
    )
    if turn.inbound is not None:
        sessions.attach(turn.inbound, chat_session, {"role": "user", "content": text})
    await sessions.collapse_if_over_budget(turn.session, chat_session, summarizer(turn))
    try:
        reply = await run_model(turn, turn.llm, chat_session)
    except LlmError as error:
        reply = Reply(t("llm_error", turn.language, error=error))
    reply.text = prefix(dropped, reply.text)
    return reply


async def run_model(turn: Turn, llm: LlmClient, chat_session: sessions.ChatSession) -> Reply:
    model = model_id(turn.settings.model)
    messages = [await system_message(turn, chat_session)]
    messages += await sessions.replay(turn.session, chat_session)
    buttons: list[Button] = []
    result: LlmResult | None = None

    for _ in range(MAX_TOOL_ROUNDS):
        result = await chat(
            llm, turn.session, turn.user.id, turn.now, messages, tools.schemas(), model
        )
        if not result.wants_tools:
            break
        tool_messages, round_buttons, changed = await run_tools(turn, result.tool_calls)
        buttons += round_buttons
        await log_round(turn, chat_session, result, tool_messages)
        messages += [result.message, *tool_messages]
        if changed:
            await record_change(turn)
    else:
        logger.warning("tool rounds exhausted for user %s; forcing a text answer", turn.user.id)
        result = await chat(
            llm,
            turn.session,
            turn.user.id,
            turn.now,
            messages,
            tools.schemas(),
            model,
            tool_choice="none",
        )

    text = result.text.strip() if result is not None else ""
    if not text:
        logger.warning("model %s returned an empty answer", model)
        text = t("llm_empty_reply", turn.language)
    # Each parked action came back with the pair as it stood then; one pair for all of them.
    buttons = [button for button in buttons if not tools.is_confirm_button(button)]
    pending = await tools.pending_action(turn)
    if pending is not None:
        buttons += tools.confirm_buttons(pending, turn.language)
    return Reply(text, buttons=buttons)


async def record_change(turn: Turn) -> None:
    """The collection round after a bot action: its diff is the bot's `task_events`.

    The action itself already succeeded; a failure here loses attribution, not
    data (the scheduled round records the change as `snapshot_diff`), so it must
    not turn a done action into an error for the user.
    """
    try:
        await collect(turn.habitica, turn.session, turn.user.id, turn.now, source=BOT_SOURCE)
    except (HabiticaError, httpx.HTTPError) as error:
        logger.warning("collection after a bot action failed: %s", error)


async def run_tools(
    turn: Turn, calls: list[ToolCall]
) -> tuple[list[dict[str, Any]], list[Button], bool]:
    """Dispatch one round of tool calls; returns the tool messages for the model."""
    tool_messages: list[dict[str, Any]] = []
    buttons: list[Button] = []
    changed = False
    for call in calls:
        outcome = await tools.dispatch(turn, call)
        buttons += outcome.buttons
        changed = changed or outcome.changed_tasks
        tool_messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(outcome.content, ensure_ascii=False),
            }
        )
    return tool_messages, buttons, changed


async def log_round(
    turn: Turn,
    chat_session: sessions.ChatSession,
    result: LlmResult,
    tool_messages: list[dict[str, Any]],
) -> None:
    """The assistant's tool calls and their results, as session rows, together."""
    described = "; ".join(
        f"{call.name}({tools.compact(call.arguments)})" for call in result.tool_calls
    )
    await sessions.append(
        turn.session, chat_session, turn.now, "out", "tool", described, result.message
    )
    for message in tool_messages:
        await sessions.append(
            turn.session, chat_session, turn.now, "in", "tool", message["content"], message
        )


async def system_message(turn: Turn, chat_session: sessions.ChatSession) -> dict[str, Any]:
    stats = await fresh_stats(turn)
    known = open_tasks(await load_known_tasks(turn.session, turn.user.id))
    prompt = build_system_prompt(
        language=turn.language,
        now=turn.now,
        tz=turn.tz,
        stats=stats,
        tasks=known,
        recent_summaries=await sessions.recent_summaries(turn.session, turn.user.id),
        session_topic=chat_session.topic,
        session_summary=chat_session.collapsed_summary,
        skills=None if stats is None else await skills_now(turn, stats),
    )
    return {"role": "system", "content": prompt}


async def skills_now(turn: Turn, stats: UserStats) -> list[AvailableSkill]:
    """The cached game content is a file read; a missing one is not worth failing the turn."""
    try:
        content = await turn.habitica.get_content()
    except (HabiticaError, httpx.HTTPError) as error:
        logger.warning("could not load the game content for the skills: %s", error)
        return []
    return available_skills(stats, content)


async def fresh_stats(turn: Turn) -> UserStats | None:
    """Stats from a snapshot at most ten minutes old; None when Habitica is unreachable."""
    try:
        snapshot = await collect_if_stale(
            turn.habitica, turn.session, turn.user.id, turn.now, tools.STATS_MAX_AGE
        )
    except (HabiticaError, httpx.HTTPError) as error:
        logger.warning("could not refresh Habitica before the turn: %s", error)
        return None
    return user_stats_from_json(snapshot.raw)


# ---- confirmation buttons ----


async def confirm(turn: Turn, payload: dict[str, Any]) -> Reply:
    pending = await matching_pending(turn, payload)
    if pending is None:
        return Reply(t("unknown_button", turn.language))
    await clear_state(turn.session, turn.user.id)
    await record_press(turn, tools.CONFIRM_ACTION)
    actions = pending["actions"]
    for index, action in enumerate(actions):
        outcome = await tools.run_action(turn, action)
        if "error" in outcome.content:
            return Reply(stopped_at(turn, actions, index, outcome.content["error"]))
        if outcome.changed_tasks:
            await record_change(turn)
    done = tools.describe_actions(actions, turn.language)
    return Reply(t("action_done", turn.language, action=done))


def stopped_at(turn: Turn, actions: list[dict[str, Any]], index: int, error: str) -> str:
    """What ran, what failed and why, and what was not tried."""
    lang = turn.language
    lines = []
    if index > 0:
        lines.append(t("action_done", lang, action=tools.describe_actions(actions[:index], lang)))
    failed = tools.describe_action(actions[index], lang)
    lines.append(t("action_failed", lang, action=failed, error=error))
    if index + 1 < len(actions):
        rest = tools.describe_actions(actions[index + 1 :], lang)
        lines.append(t("action_not_tried", lang, action=rest))
    return "\n".join(lines)


async def cancel(turn: Turn, payload: dict[str, Any]) -> Reply:
    pending = await matching_pending(turn, payload)
    if pending is None:
        return Reply(t("unknown_button", turn.language))
    await clear_state(turn.session, turn.user.id)
    await record_press(turn, tools.CANCEL_ACTION)
    return Reply(
        t("action_cancelled", turn.language, action=tools.describe_pending(pending, turn.language))
    )


async def matching_pending(turn: Turn, payload: dict[str, Any]) -> dict[str, Any] | None:
    """The parked action, only if this button was made for it."""
    pending = await tools.pending_action(turn)
    if pending is None or payload.get("token") != pending.get("token"):
        return None
    return pending


async def record_press(turn: Turn, action: str) -> None:
    """A button press belongs to the conversation: the model should see it happened."""
    if turn.inbound is None:
        return
    chat_session = await sessions.ensure_session(
        turn.session, turn.user.id, turn.now, summarizer(turn)
    )
    sessions.attach(turn.inbound, chat_session, {"role": "user", "content": f"[pressed: {action}]"})


async def drop_pending(turn: Turn) -> str:
    """Any message other than the buttons drops a parked action; returns the line saying so."""
    pending = await tools.pending_action(turn)
    if pending is None:
        return ""
    await clear_state(turn.session, turn.user.id)
    return t("action_dropped", turn.language, action=tools.describe_pending(pending, turn.language))


def prefix(line: str, text: str) -> str:
    return f"{line}\n{text}" if line else text
