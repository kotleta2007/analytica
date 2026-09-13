"""What the model can do, as function tools: schemas, dispatch, confirmation.

Each tool is a small async function of the turn and the model's arguments
that returns a `ToolResult`: what the model gets to read (a JSON object),
whether tasks changed (the harness then collects a fresh snapshot, which
also writes the `task_events` rows with `source=bot`), and any buttons the
reply should carry.

Destructive tools never act on the first call. They store the action in
`conversation_state` and answer `needs_confirmation`; the user sees
✅ Do it / ❌ Cancel, and only the button runs the tool with `confirmed=True`.
The prompt is never trusted to enforce this.
"""

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.collector.snapshots import collect_if_stale
from app.core.i18n import format_day, format_moment, parse_day, t
from app.core.replies import Button
from app.core.state import get_state, set_state
from app.core.tasks import (
    KnownTask,
    TaskNotFound,
    due_datetime,
    due_day,
    load_known_tasks,
    open_tasks,
    resolve_task,
)
from app.core.turn import Turn
from app.habitica.client import HabiticaError
from app.habitica.types import Task, user_stats_from_json
from app.llm.client import ToolCall

logger = logging.getLogger(__name__)

PENDING_CONFIRM = "pending_confirm"
CONFIRM_ACTION = "confirm"
CANCEL_ACTION = "cancel"
STATS_MAX_AGE = timedelta(minutes=10)

DIFFICULTY_PRIORITY = {"trivial": 0.1, "easy": 1.0, "medium": 1.5, "hard": 2.0}
REPEAT_KEYS = {"mon": "m", "tue": "t", "wed": "w", "thu": "th", "fri": "f", "sat": "s", "sun": "su"}


class ToolArgumentError(ValueError):
    """The model passed something the tool cannot use; it gets told and may retry."""


@dataclass
class ToolResult:
    content: dict[str, Any]
    changed_tasks: bool = False
    buttons: list[Button] = field(default_factory=list)


ToolFunction = Callable[[Turn, dict[str, Any], bool], Awaitable[ToolResult]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    run: ToolFunction
    destructive: bool = False

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", **self.parameters},
            },
        }


# ---- dispatch ----


async def dispatch(turn: Turn, call: ToolCall) -> ToolResult:
    """Run one tool call from the model; errors come back as content, never raise."""
    tool = TOOLS.get(call.name)
    if tool is None:
        return error(f"unknown tool {call.name!r}")
    if "_unparsable_arguments" in call.arguments:
        return error("arguments were not valid JSON; send them again")
    logger.info("tool %s(%s)", call.name, compact(call.arguments))
    return await run_tool(turn, tool, call.arguments, confirmed=False)


async def run_tool(
    turn: Turn, tool: Tool, arguments: dict[str, Any], confirmed: bool
) -> ToolResult:
    try:
        return await tool.run(turn, arguments, confirmed)
    except (ToolArgumentError, TaskNotFound) as problem:
        return error(str(problem))
    except HabiticaError as problem:
        logger.warning("tool %s: Habitica refused: %s", tool.name, problem)
        return error(f"Habitica refused: {problem.message}")
    except httpx.HTTPError as problem:
        logger.warning("tool %s: Habitica unreachable: %s", tool.name, problem)
        return error("Habitica could not be reached; try again later")


def error(message: str) -> ToolResult:
    return ToolResult({"error": message})


def schemas() -> list[dict[str, Any]]:
    return [tool.schema() for tool in TOOLS.values()]


# ---- confirmation ----


async def request_confirmation(
    turn: Turn, tool_name: str, arguments: dict[str, Any], describe_key: str, **values: str
) -> ToolResult:
    """Park the action in `conversation_state`; the buttons carry a token so a
    stale button from an earlier offer cannot trigger a newer one."""
    token = uuid.uuid4().hex[:12]
    await set_state(
        turn.session,
        turn.user.id,
        PENDING_CONFIRM,
        {
            "tool": tool_name,
            "arguments": arguments,
            "describe": {"key": describe_key, "values": values},
            "token": token,
        },
        turn.now,
    )
    return ToolResult(
        content={
            "status": "needs_confirmation",
            "action": t(describe_key, "en", **values),
            "instructions": "The user now sees Do it / Cancel buttons. Tell them in one short "
            "sentence what will happen if they confirm, then stop; do not call the tool again.",
        },
        buttons=[
            Button(t("confirm_do_it", turn.language), CONFIRM_ACTION, {"token": token}),
            Button(t("confirm_cancel", turn.language), CANCEL_ACTION, {"token": token}),
        ],
    )


async def pending_action(turn: Turn) -> dict[str, Any] | None:
    state = await get_state(turn.session, turn.user.id)
    if state is None or state.state != PENDING_CONFIRM:
        return None
    return state.payload


async def run_pending(turn: Turn, pending: dict[str, Any]) -> ToolResult:
    """The user pressed Do it: run the parked action for real."""
    tool = TOOLS[pending["tool"]]
    logger.info("confirmed %s(%s)", tool.name, compact(pending["arguments"]))
    return await run_tool(turn, tool, pending["arguments"], confirmed=True)


def describe_pending(pending: dict[str, Any], lang: str) -> str:
    describe = pending["describe"]
    return t(describe["key"], lang, **describe["values"])


# ---- the tools ----


async def list_tasks(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    wanted = args.get("type")
    if wanted not in (None, "habit", "daily", "todo"):
        raise ToolArgumentError("type must be habit, daily or todo")
    known = open_tasks(await load_known_tasks(turn.session, turn.user.id))
    tasks = [task_details(k, turn.tz) for k in known if wanted is None or k.task.type == wanted]
    return ToolResult({"tasks": tasks, "count": len(tasks)})


async def score_task(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    direction = args.get("direction", "up")
    if direction not in ("up", "down"):
        raise ToolArgumentError("direction must be up or down")
    known = await resolve_task(turn.session, turn.user.id, required(args, "task_id"))
    result = await turn.habitica.score_task(known.row.habitica_id, direction)
    return ToolResult(
        {
            "task": known.task.text,
            "type": known.task.type,
            "result": scoring_outcome(known.task, direction),
            "delta": round(result.delta, 2),
            "stats_now": {
                "hp": round(result.hp, 1),
                "mp": round(result.mp, 1),
                "exp": round(result.exp, 1),
                "gp": round(result.gp, 1),
                "lvl": result.lvl,
            },
        },
        changed_tasks=True,
    )


def scoring_outcome(task: Task, direction: str) -> str:
    if task.type == "habit":
        return "counted +1" if direction == "up" else "counted -1"
    return "completed" if direction == "up" else "marked not done"


async def create_task(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    task_type = required(args, "type")
    if task_type not in ("habit", "daily", "todo"):
        raise ToolArgumentError("type must be habit, daily or todo")
    payload: dict[str, Any] = {"type": task_type, "text": required(args, "text")}
    payload.update(optional_fields(args, task_type, turn.tz))
    if task_type == "habit":
        payload.setdefault("up", True)
        payload.setdefault("down", False)
    task = await turn.habitica.create_task(payload)
    return ToolResult({"created": task_summary(task, turn.tz)}, changed_tasks=True)


async def update_task(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    known = await resolve_task(turn.session, turn.user.id, required(args, "task_id"))
    payload = optional_fields(args, known.task.type, turn.tz)
    if "text" in args:
        payload["text"] = required(args, "text")
    if not payload:
        raise ToolArgumentError("nothing to update: pass text, notes, due_date or difficulty")
    task = await turn.habitica.update_task(known.row.habitica_id, payload)
    return ToolResult({"updated": task_summary(task, turn.tz)}, changed_tasks=True)


async def delete_task(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    known = await resolve_task(turn.session, turn.user.id, required(args, "task_id"))
    if not confirmed:
        return await request_confirmation(
            turn,
            "delete_task",
            {"task_id": known.row.habitica_id},
            "action_delete_task",
            text=known.task.text,
        )
    await turn.habitica.delete_task(known.row.habitica_id)
    return ToolResult({"deleted": known.task.text}, changed_tasks=True)


async def get_stats(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    snapshot = await collect_if_stale(
        turn.habitica, turn.session, turn.user.id, turn.now, STATS_MAX_AGE
    )
    user = user_stats_from_json(snapshot.raw)
    return ToolResult(
        {
            "hp": round(user.hp, 1),
            "max_hp": user.max_hp,
            "mp": round(user.mp, 1),
            "max_mp": user.max_mp,
            "exp": round(user.exp, 1),
            "exp_to_next_level": user.to_next_level,
            "gp": round(user.gp, 1),
            "lvl": user.lvl,
            "class": user.class_name,
            "as_of": format_moment(snapshot.taken_at.astimezone(turn.tz)),
        }
    )


# ---- arguments and shapes ----


def required(args: dict[str, Any], name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ToolArgumentError(f"{name} is required")
    return value.strip()


def optional_fields(args: dict[str, Any], task_type: str, tz: ZoneInfo) -> dict[str, Any]:
    """The Habitica fields behind the optional arguments create and update share."""
    payload: dict[str, Any] = {}
    if "notes" in args and args["notes"] is not None:
        payload["notes"] = str(args["notes"])
    if args.get("due_date"):
        if task_type != "todo":
            raise ToolArgumentError("only todos have a due date")
        # Local midnight in UTC: what Habitica's own UI stores, so the app shows the same day.
        payload["date"] = due_datetime(parse_date(args["due_date"]), tz).isoformat()
    if args.get("difficulty"):
        if args["difficulty"] not in DIFFICULTY_PRIORITY:
            raise ToolArgumentError("difficulty must be trivial, easy, medium or hard")
        payload["priority"] = DIFFICULTY_PRIORITY[args["difficulty"]]
    if args.get("repeat_days"):
        if task_type != "daily":
            raise ToolArgumentError("only dailies have repeat_days")
        payload["frequency"] = "weekly"
        payload["repeat"] = repeat_from_days(args["repeat_days"])
    return payload


def parse_date(value: Any) -> date:
    try:
        return parse_day(str(value))
    except ValueError as problem:
        raise ToolArgumentError("due_date must be DD.MM.YYYY or YYYY-MM-DD") from problem


def repeat_from_days(days: Any) -> dict[str, bool]:
    if not isinstance(days, list) or not days:
        raise ToolArgumentError("repeat_days must be a non-empty list of weekday names")
    chosen = set()
    for day in days:
        key = REPEAT_KEYS.get(str(day).lower()[:3])
        if key is None:
            raise ToolArgumentError(f"unknown weekday {day!r}; use mon..sun")
        chosen.add(key)
    return {key: key in chosen for key in REPEAT_KEYS.values()}


def task_summary(task: Task, tz: ZoneInfo) -> dict[str, Any]:
    summary: dict[str, Any] = {"id": task.id[:8], "type": task.type, "text": task.text}
    if task.notes:
        summary["notes"] = task.notes
    if (day := due_day(task, tz)) is not None:
        summary["due"] = format_day(day)
    return summary


def task_details(known: KnownTask, tz: ZoneInfo) -> dict[str, Any]:
    task = known.task
    details = task_summary(task, tz)
    details["id"] = known.short_id
    if task.type == "daily":
        details["due_today"] = bool(task.is_due)
        details["completed"] = bool(task.completed)
        details["streak"] = task.streak or 0
    elif task.type == "habit":
        details["counter_up"] = task.counter_up or 0
        details["counter_down"] = task.counter_down or 0
    if task.checklist:
        details["checklist"] = [
            {"text": item.text, "done": item.completed} for item in task.checklist
        ]
    return details


def compact(arguments: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in arguments.items())


TASK_ID = {
    "type": "string",
    "description": "The task id as shown in the task list (the short form is fine).",
}

TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            name="list_tasks",
            description="The user's open tasks with ids, notes, due dates, streaks and "
            "checklists. Optionally only one type.",
            parameters={
                "properties": {
                    "type": {"type": "string", "enum": ["habit", "daily", "todo"]},
                },
            },
            run=list_tasks,
        ),
        Tool(
            name="score_task",
            description="Complete a daily or todo (up), undo that (down), or count a habit "
            "(+ is up, - is down).",
            parameters={
                "properties": {
                    "task_id": TASK_ID,
                    "direction": {"type": "string", "enum": ["up", "down"]},
                },
                "required": ["task_id", "direction"],
            },
            run=score_task,
        ),
        Tool(
            name="create_task",
            description="Create a habit, daily or todo. The text must be in English.",
            parameters={
                "properties": {
                    "type": {"type": "string", "enum": ["habit", "daily", "todo"]},
                    "text": {"type": "string", "description": "Title, in English."},
                    "notes": {"type": "string"},
                    "due_date": {
                        "type": "string",
                        "description": "DD.MM.YYYY or YYYY-MM-DD; todos only.",
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": ["trivial", "easy", "medium", "hard"],
                    },
                    "repeat_days": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": 'Dailies only: weekdays like ["mon", "wed", "fri"]. '
                        "Omit for every day.",
                    },
                },
                "required": ["type", "text"],
            },
            run=create_task,
        ),
        Tool(
            name="update_task",
            description="Change a task's text, notes, due date or difficulty.",
            parameters={
                "properties": {
                    "task_id": TASK_ID,
                    "text": {"type": "string", "description": "New title, in English."},
                    "notes": {"type": "string"},
                    "due_date": {
                        "type": "string",
                        "description": "DD.MM.YYYY or YYYY-MM-DD; todos only.",
                    },
                    "difficulty": {
                        "type": "string",
                        "enum": ["trivial", "easy", "medium", "hard"],
                    },
                },
                "required": ["task_id"],
            },
            run=update_task,
        ),
        Tool(
            name="delete_task",
            description="Delete a task. The user must confirm with a button first.",
            parameters={"properties": {"task_id": TASK_ID}, "required": ["task_id"]},
            run=delete_task,
            destructive=True,
        ),
        Tool(
            name="get_stats",
            description="Current HP, mana, experience, gold and level.",
            parameters={"properties": {}},
            run=get_stats,
        ),
    )
}
