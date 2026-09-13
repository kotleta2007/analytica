"""The system prompt, built fresh for every call.

It carries what the model must know and nothing it can ask a tool for: who it
is, the reply language and the one-reply override, the English-content rule,
the clock, one line of stats, the compact task list, and the summaries of the
last few closed sessions.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.i18n import format_day, format_moment
from app.core.tasks import KnownTask, due_day
from app.habitica.types import Task, UserStats

LANGUAGE_NAMES = {"en": "English", "ru": "Russian", "fr": "French"}

ROLE = (
    "You are Habitica Analytica, the user's personal coach and assistant for their Habitica "
    "account, talking in a chat. Be brief and concrete: short sentences, no motivational "
    "filler, no lists of options unless asked."
)
OVERRIDE_RULE = (
    "If the user's message explicitly asks for another language, obey for that reply only."
)
DATE_RULE = "Write dates as DD.MM.YYYY (31.12.2026), never in ISO form."
CONTENT_RULE = (
    "Habitica content is always English: titles and notes you create or edit must be in "
    "English, so translate the user's words. When the user refers to a task in another "
    "language, match it semantically against the English task list below. Use the ids exactly "
    "as shown; never invent one."
)
TOOL_RULE = (
    "Use the tools for anything that reads or changes Habitica and report what they returned; "
    "never claim something happened without a tool result. Ask before acting only when the "
    "user's intent is genuinely ambiguous. When a tool answers needs_confirmation, the user "
    "sees Do it / Cancel buttons: say in one sentence what will happen if they confirm, then "
    "stop. When a tool reports an error, say so plainly."
)


def build_system_prompt(
    *,
    language: str,
    now: datetime,
    tz: ZoneInfo,
    stats: UserStats | None,
    tasks: list[KnownTask],
    recent_summaries: list[str],
    session_topic: str | None = None,
    session_summary: str | None = None,
) -> str:
    parts = [
        ROLE,
        f"Reply in {LANGUAGE_NAMES.get(language, 'English')}. {OVERRIDE_RULE} {DATE_RULE}",
        CONTENT_RULE,
        TOOL_RULE,
        clock_line(now, tz, stats),
    ]
    if stats is not None:
        parts.append("Stats: " + stats_line(stats))
    parts.append(task_list(tasks, tz))
    if recent_summaries:
        parts.append("Recent context, most recent first:\n" + bullets(recent_summaries))
    if session_topic:
        parts.append(f"This is a planning session about: {session_topic}.")
    if session_summary:
        parts.append("Earlier in this conversation (summarised):\n" + session_summary.strip())
    return "\n\n".join(parts)


def clock_line(now: datetime, tz: ZoneInfo, stats: UserStats | None) -> str:
    local = now.astimezone(tz)
    line = f"Now: {local.strftime('%A')} {format_moment(local)} ({tz.key})."
    if stats is not None:
        line += f" The Habitica day starts at {stats.day_start:02d}:00."
    return line


def stats_line(stats: UserStats) -> str:
    return (
        f"HP {stats.hp:.0f}/{stats.max_hp} · MP {stats.mp:.0f}/{stats.max_mp} · "
        f"XP {stats.exp:.0f}/{stats.to_next_level} · {stats.gp:.0f} GP · "
        f"level {stats.lvl} {stats.class_name}"
    )


def task_list(tasks: list[KnownTask], tz: ZoneInfo) -> str:
    if not tasks:
        return "Tasks: none."
    return "Tasks (id type · text · state):\n" + "\n".join(task_line(k, tz) for k in tasks)


def task_line(known: KnownTask, tz: ZoneInfo) -> str:
    task = known.task
    line = f"- {known.short_id} {task.type} · {task.text}"
    state = task_state(task, tz)
    return f"{line} · {state}" if state else line


def task_state(task: Task, tz: ZoneInfo) -> str:
    if task.type == "daily":
        due = "due today" if task.is_due else "not due today"
        done = "done" if task.completed else "not done"
        return f"{due}, {done}, streak {task.streak or 0}"
    if task.type == "todo" and (day := due_day(task, tz)) is not None:
        return f"due {format_day(day)}"
    return ""


def bullets(lines: list[str]) -> str:
    return "\n".join(f"- {line.strip()}" for line in lines)
