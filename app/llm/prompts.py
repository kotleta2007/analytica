"""The system prompt, built fresh for every call.

It carries what the model must know and nothing it can ask a tool for: who it
is, the reply language and the one-reply override, the English-content rule,
the clock, one line of stats, the compact task list, and the summaries of the
last few closed sessions.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from app.core.i18n import format_day, format_moment
from app.core.skills import AvailableSkill, skill_line
from app.core.tasks import KnownTask, due_day
from app.habitica.types import Task, UserStats, class_label

# Named unambiguously, with the words the Habitica app itself shows in that language
# (from its locale files): the model otherwise drifts into a Russian/Ukrainian mix and
# transliterates the English terms it sees in the task list.
LANGUAGE_LINES = {
    "en": "Reply in English.",
    "ru": (
        "Reply in Russian (русский) — never Ukrainian, never a mix of languages, no "
        "transliterated English. Use the words the Habitica app shows in Russian: привычка "
        "(habit), ежедневное дело (daily), задача (todo), награда (reward), серия (streak), "
        "список (checklist), начало суток (day start, when cron runs); здоровье (HP), мана "
        "(MP), опыт (XP), золото (gold), уровень (level); воин, маг, разбойник, целитель "
        "(the classes)."
    ),
    "fr": (
        "Reply in French (français). Use the words the Habitica app shows in French: habitude "
        "(habit), quotidienne (daily), à faire (todo), récompense (reward), combo (streak), "
        "liste de vérification (checklist), début de journée (day start, when cron runs); "
        "santé (HP), mana (MP), expérience (XP), or (gold), niveau (level); guerrier, mage, "
        "voleur, guérisseur (the classes)."
    ),
}

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
PRIMER_RULES = (
    "Three task types. A HABIT is scored any number of times a day with + (good) or - (bad, "
    "only if the habit allows it); it has no schedule and no penalty for being ignored. A DAILY "
    "is due on the days it repeats (every day, chosen weekdays, or every X days); each due daily "
    "is either completed or missed. A TODO is done once; it may have a due date.",
    "The Habitica day does not start at midnight but at the user's day start (given below). At "
    'that moment "cron" runs: every daily that was due and not completed costs HP, all '
    "dailies reset to not done, and mana regenerates. This is the only damage in the game. Cron "
    "does not run by itself: only an explicit request runs it. Half an hour before day start "
    "this bot asks the user which unticked dailies they did (they can still be ticked until "
    "cron), and it runs cron one minute after day start or once they have answered, so the "
    "user never has to open the app for the reset.",
    "Todos never cost HP, overdue or not: an old todo turns from yellow toward red and is worth "
    "more gold and XP when finally completed. A todo due today is overdue after midnight, not "
    "at day start.",
    "Completing a daily or todo, or scoring a habit +, gives XP and gold; unticking takes them "
    "back. A daily's streak grows by one per completed due day and breaks on a miss. Completing "
    'every due daily is a "perfect day" and gives a small bonus.',
    "HP 0 means death: the user loses a level, all gold, and one piece of equipment. Difficulty "
    "(trivial, easy, medium, hard) scales both the reward and the damage of a task. A checklist "
    "inside a task adds rewards per ticked item and lowers a daily's damage in proportion to "
    "the ticked share.",
    "Stats: HP (health, max 50), MP (mana, spent on class skills), XP toward the next level, GP "
    "(gold, spent on rewards and gear). Classes are warrior, mage, rogue and healer; INT raises "
    "max mana and XP, PER raises gold and drops, CON reduces damage, STR raises rewards and "
    "critical hits.",
    "Skills: each class unlocks four between levels 11 and 14. A cast costs mana; some act on "
    "one task (Burst of Flames, Brutal Smash, Pickpocket, Backstab), the others on the user, "
    "the party or all tasks. The skills the user can cast now are listed with their stats, with "
    "the mana already compared; a cast always asks for a confirm button.",
    "Rewards are things the user buys with gold; they are not tasks and this bot does not "
    "manage them.",
    "Each list (habits, dailies, todos) keeps the order the user gave it, top to bottom; "
    '"put X first" or "move X to the bottom" is an action (move_task), not a preference '
    "to remember.",
)
PRIMER = (
    "How Habitica works (you cannot look this up; reason from these rules only):\n"
    + "\n".join(f"- {rule}" for rule in PRIMER_RULES)
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
    skills: list[AvailableSkill] | None = None,
) -> str:
    parts = [
        ROLE,
        f"{LANGUAGE_LINES.get(language, LANGUAGE_LINES['en'])} {OVERRIDE_RULE} {DATE_RULE}",
        CONTENT_RULE,
        PRIMER,
        TOOL_RULE,
        clock_line(now, tz, stats),
    ]
    if stats is not None:
        parts.append("The user's stats now: " + stats_line(stats))
    if skills:
        parts.append(skills_block(skills))
    parts.append(task_list(tasks, tz))
    if recent_summaries:
        parts.append("Recent context, most recent first:\n" + bullets(recent_summaries))
    if session_topic:
        parts.append(f"This is a planning session about: {session_topic}.")
    if session_summary:
        parts.append("Earlier in this conversation (summarised):\n" + session_summary.strip())
    return "\n\n".join(parts)


def skills_block(skills: list[AvailableSkill]) -> str:
    lines = []
    for available in skills:
        state = "castable now" if available.castable else "not enough mana"
        lines.append(f"- {skill_line(available)} [{state}]")
    return "Class skills unlocked (cast_skill):\n" + "\n".join(lines)


def clock_line(now: datetime, tz: ZoneInfo, stats: UserStats | None) -> str:
    local = now.astimezone(tz)
    line = f"Now: {local.strftime('%A')} {format_moment(local)} ({tz.key})."
    if stats is not None:
        line += f" The Habitica day starts at {stats.day_start:02d}:00."
    return line


def stats_line(stats: UserStats) -> str:
    """Every derived number is computed here; the model is not trusted with arithmetic."""
    return (
        f"HP {stats.hp:.0f}/{stats.max_hp} · MP {stats.mp:.0f}/{stats.max_mp} · "
        f"XP {stats.exp:.0f}/{stats.to_next_level} ({stats.exp_remaining} more XP to reach "
        f"level {stats.lvl + 1}) · {stats.gp:.0f} GP · "
        f"level {stats.lvl} {class_label(stats.class_name)}"
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
