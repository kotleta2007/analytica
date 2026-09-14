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
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.collector.snapshots import collect_if_stale, remember_list_order
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
from app.habitica.types import Task, class_label, user_stats_from_json
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
    if args.get("checklist") is not None:
        if task_type == "habit":
            raise ToolArgumentError("habits have no checklist")
        payload["checklist"] = [{"text": item} for item in checklist_texts(args["checklist"])]
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
    if args.get("checklist") is not None:
        if known.task.type == "habit":
            raise ToolArgumentError("habits have no checklist")
        payload["checklist"] = replacement_checklist(known.task, checklist_texts(args["checklist"]))
    if not payload:
        raise ToolArgumentError(
            "nothing to update: pass text, notes, due_date, difficulty or checklist"
        )
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


async def move_task(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    known = await resolve_task(turn.session, turn.user.id, required(args, "task_id"))
    position = parse_position(args.get("position"))
    order = await turn.habitica.move_task(known.row.habitica_id, position)
    # Habitica answers with the list's new order; remembering it is all a move needs.
    await remember_list_order(turn.session, turn.user.id, order)
    where = "top" if position == 0 else "bottom" if position == -1 else f"position {position + 1}"
    return ToolResult(
        {
            "moved": known.task.text,
            "to": f"{where} of the {known.task.type} list",
            "list_length": len(order),
        }
    )


def parse_position(value: Any) -> int:
    """`top` → 0, `bottom` → -1, or a 1-based slot as the user would count it."""
    if value in ("top", None):
        return 0
    if value == "bottom":
        return -1
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        return value - 1
    if isinstance(value, str) and value.isdigit() and int(value) >= 1:
        return int(value) - 1
    raise ToolArgumentError("position must be top, bottom, or a slot number from 1")


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
            "exp_needed_for_next_level": user.to_next_level,
            "exp_remaining_to_next_level": user.exp_remaining,
            "gp": round(user.gp, 1),
            "lvl": user.lvl,
            "next_lvl": user.lvl + 1,
            "class": class_label(user.class_name),
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
        payload["notes"] = markdown_notes(str(args["notes"]))
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


def markdown_notes(notes: str) -> str:
    """Habitica renders notes as Markdown: `•` is not a list marker and a lone newline is not
    a line break, so the model's bullet lines would collapse into one line. Make them lists."""
    lines = [re.sub(r"^\s*[•·▪‣]\s*", "- ", line) for line in notes.strip().splitlines()]
    out: list[str] = []
    for previous, line in zip(["", *lines], lines, strict=False):
        both_text = bool(previous.strip() and line.strip())
        if both_text and not (is_list_line(previous) and is_list_line(line)):
            out.append("")  # a blank line, or Markdown joins the two into one paragraph
        out.append(line)
    return "\n".join(out)


def is_list_line(line: str) -> bool:
    return re.match(r"^\s*([-*+]|\d+[.)])\s", line) is not None


def checklist_texts(value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ToolArgumentError("checklist must be a list of strings")
    texts = [item.strip() for item in value if item.strip()]
    if not texts:
        raise ToolArgumentError("checklist must have at least one item")
    return texts


def replacement_checklist(task: Task, texts: list[str]) -> list[dict[str, Any]]:
    """The whole checklist as Habitica's PUT wants it; an item whose text is unchanged keeps
    its id and its tick, everything else is new and unticked."""
    existing = {item.text: item for item in task.checklist}
    items: list[dict[str, Any]] = []
    for text in texts:
        kept = existing.get(text)
        if kept is not None:
            items.append({"id": kept.id, "text": text, "completed": kept.completed})
        else:
            items.append({"text": text, "completed": False})
    return items


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
    if task.checklist:
        summary["checklist"] = [
            {"text": item.text, "done": item.completed} for item in task.checklist
        ]
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
    return details


def compact(arguments: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in arguments.items())


NOTES_HELP = (
    "Free text under the title, in English; Markdown. Not for sub-items — those go in checklist."
)
CHECKLIST_HELP = (
    "Sub-items shown as tickable checklist entries in the app, in English. When the user "
    "gives bullet points, steps or subtasks for a task, they belong here, never in notes."
)

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
                    "notes": {"type": "string", "description": NOTES_HELP},
                    "checklist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": CHECKLIST_HELP,
                    },
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
                    "notes": {"type": "string", "description": NOTES_HELP},
                    "checklist": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": CHECKLIST_HELP + " Replaces the whole checklist; items "
                        "whose text is unchanged keep their tick.",
                    },
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
            name="move_task",
            description="Reorder a task within its list (habits, dailies and todos each have "
            "their own order), as dragging it in the Habitica app would. To put several tasks "
            "at the top in a given order, give them slot numbers 1, 2, 3… in one round; moving "
            'each to "top" in turn would reverse them.',
            parameters={
                "properties": {
                    "task_id": TASK_ID,
                    "position": {
                        "description": '"top", "bottom", or a slot number counted from 1.',
                        "anyOf": [{"type": "string"}, {"type": "integer"}],
                    },
                },
                "required": ["task_id", "position"],
            },
            run=move_task,
        ),
        Tool(
            name="get_stats",
            description="Current HP, mana, experience, gold and level.",
            parameters={"properties": {}},
            run=get_stats,
        ),
    )
}
