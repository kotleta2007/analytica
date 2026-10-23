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
from app.core import glossary
from app.core.i18n import format_day, format_moment, parse_day, t
from app.core.replies import Button
from app.core.skills import available_skills, find_skill
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
    """Park the action in `conversation_state`, behind whatever this turn parked already.

    A turn starts by dropping any earlier parked action (`conversation.answer`), so a
    pending entry seen here was parked by this turn: "cast it 5 times" is five calls and
    one ✅ Do all 5. The buttons carry a token, so a stale button from an earlier offer
    cannot trigger a newer one.
    """
    action = {
        "tool": tool_name,
        "arguments": arguments,
        "describe": {"key": describe_key, "values": values},
    }
    pending = await pending_action(turn) or {"actions": [], "token": uuid.uuid4().hex[:12]}
    pending["actions"] = [*pending["actions"], action]
    await set_state(turn.session, turn.user.id, PENDING_CONFIRM, pending, turn.now)
    return ToolResult(
        content={
            "status": "needs_confirmation",
            "action": t(describe_key, "en", **values),
            "waiting_for_the_button": len(pending["actions"]),
            "instructions": "Nothing has run yet. The user sees one Do it / Cancel pair for "
            "every action waiting in this turn; one press runs them all, in order. Call the "
            "tool once per action (five casts = five calls), then tell them in one short "
            "sentence what one press will do, and stop; do not call it again for an action "
            "already waiting.",
        },
        buttons=confirm_buttons(pending, turn.language),
    )


def confirm_buttons(pending: dict[str, Any], lang: str) -> list[Button]:
    count = len(pending["actions"])
    label = t("confirm_do_it", lang) if count == 1 else t("confirm_do_all", lang, count=count)
    return [
        Button(label, CONFIRM_ACTION, {"token": pending["token"]}),
        Button(t("confirm_cancel", lang), CANCEL_ACTION, {"token": pending["token"]}),
    ]


def is_confirm_button(button: Button) -> bool:
    return button.action in (CONFIRM_ACTION, CANCEL_ACTION)


async def pending_action(turn: Turn) -> dict[str, Any] | None:
    """`{"actions": [{tool, arguments, describe}, …], "token": …}`, or None."""
    state = await get_state(turn.session, turn.user.id)
    if state is None or state.state != PENDING_CONFIRM:
        return None
    payload = dict(state.payload)
    if "actions" not in payload:
        # Parked by the bot before P06, one action at the top level; still pressable.
        action = {key: payload[key] for key in ("tool", "arguments", "describe")}
        payload = {"actions": [action], "token": payload["token"]}
    return payload


async def run_action(turn: Turn, action: dict[str, Any]) -> ToolResult:
    """The user pressed Do it: run one parked action for real, with every check again."""
    tool = TOOLS[action["tool"]]
    logger.info("confirmed %s(%s)", tool.name, compact(action["arguments"]))
    return await run_tool(turn, tool, action["arguments"], confirmed=True)


def describe_action(action: dict[str, Any], lang: str) -> str:
    describe = action["describe"]
    return t(describe["key"], lang, **describe["values"])


def describe_actions(actions: list[dict[str, Any]], lang: str) -> str:
    """Each distinct action once, in order; a repeated one gets `×5`."""
    counts: dict[str, int] = {}
    for action in actions:
        text = describe_action(action, lang)
        counts[text] = counts.get(text, 0) + 1
    return "; ".join(text if n == 1 else f"{text} ×{n}" for text, n in counts.items())


def describe_pending(pending: dict[str, Any], lang: str) -> str:
    return describe_actions(pending["actions"], lang)


async def mana_waiting(turn: Turn) -> int:
    """Mana already promised to the casts parked in this turn."""
    pending = await pending_action(turn)
    if pending is None:
        return 0
    return sum(
        int(action["describe"]["values"]["mana"])
        for action in pending["actions"]
        if action["tool"] == "cast_skill"
    )


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


async def cast_skill(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    """A class skill by name or key; task skills need `task_id`, the others refuse one.

    Always confirmed first: mana is spent and the app shows it. The cast itself
    is followed by a collection round — stats change, and a task skill changes
    the task's value.
    """
    reference = required(args, "skill")
    stats = user_stats_from_json(
        (
            await collect_if_stale(
                turn.habitica, turn.session, turn.user.id, turn.now, STATS_MAX_AGE
            )
        ).raw
    )
    available = find_skill(available_skills(stats, await turn.habitica.get_content()), reference)
    if available is None:
        raise ToolArgumentError(
            f"no skill {reference!r} for a level {stats.lvl} {class_label(stats.class_name)}; "
            "the unlocked ones are listed in the system prompt"
        )
    skill = available.skill
    task_reference = args.get("task_id")
    if available.needs_task and not task_reference:
        raise ToolArgumentError(f"{skill.text} acts on a task; pass task_id")
    if not available.needs_task and task_reference:
        raise ToolArgumentError(f"{skill.text} does not take a task; call it without task_id")
    if not available.castable:
        raise ToolArgumentError(f"{skill.text} costs {skill.mana} MP and the user has {stats.mp:g}")
    waiting = 0 if confirmed else await mana_waiting(turn)
    if waiting and stats.mp < waiting + skill.mana:
        raise ToolArgumentError(
            f"{skill.text} costs {skill.mana} MP; the casts already waiting for the button "
            f"take {waiting} of the user's {stats.mp:g}, so this one does not fit"
        )
    known = (
        None
        if task_reference is None
        else await resolve_task(turn.session, turn.user.id, task_reference)
    )
    target = "" if known is None else known.task.text
    if not confirmed:
        return await request_confirmation(
            turn,
            "cast_skill",
            {"skill": skill.key, "task_id": None if known is None else known.row.habitica_id},
            "action_cast_skill" if known is None else "action_cast_skill_on",
            skill=skill.text,
            mana=str(skill.mana),
            text=target,
        )
    await turn.habitica.cast_skill(skill.key, None if known is None else known.row.habitica_id)
    return ToolResult(
        {"cast": skill.text, "mana_spent": skill.mana, "target": target or skill.target},
        changed_tasks=True,
    )


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


async def add_glossary_term(turn: Turn, args: dict[str, Any], confirmed: bool) -> ToolResult:
    term = required(args, "term")
    try:
        added = await glossary.add_term(
            turn.session, turn.user.id, term, glossary.FROM_TOOL, turn.now
        )
    except glossary.GlossaryTermError as problem:
        raise ToolArgumentError(str(problem)) from None
    return ToolResult({"term": glossary.normalise(term), "added": added})


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
            name="cast_skill",
            description="Cast one of the user's class skills (listed in the system prompt with "
            "their mana cost). Task skills — Burst of Flames, Brutal Smash, Pickpocket, Backstab "
            "— need task_id; the others must not get one. To cast several times, call it once "
            "per cast, all in the same response: one button then confirms them all.",
            parameters={
                "properties": {
                    "skill": {"type": "string", "description": "Skill name or key."},
                    "task_id": TASK_ID,
                },
                "required": ["skill"],
            },
            run=cast_skill,
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
        Tool(
            name="add_glossary_term",
            description="Remember a word or name the voice recogniser misheard, so future "
            "transcripts spell it right. Use when the user says a transcript got a term wrong.",
            parameters={
                "properties": {"term": {"type": "string"}},
                "required": ["term"],
            },
            run=add_glossary_term,
        ),
    )
}
