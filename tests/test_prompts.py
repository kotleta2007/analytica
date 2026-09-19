from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.core.tasks import KnownTask
from app.db.models import TaskRow
from app.habitica.types import user_stats_from_json
from app.llm.prompts import OVERRIDE_RULE, build_system_prompt, task_line
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task

NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Zurich")


def known(task_type: str, text: str, task_id: str, **fields: object) -> KnownTask:
    task = make_task(task_type, text, task_id=task_id, **fields)
    row = TaskRow(habitica_id=task.id, type=task.type, text=task.text, raw=task.raw)
    return KnownTask(row=row, task=task)


TASKS = [
    known("daily", "Vacuum the floors", "d1a2b3c4-0000-4000-8000-000000000001", streak=4),
    known("habit", "Workout", "ab1a2b3c-0000-4000-8000-000000000002"),
    # Habitica stores a due day as local midnight in UTC: due 14/09 in Zurich is 13/09 22:00Z.
    known(
        "todo", "Buy milk", "70d0aaaa-0000-4000-8000-000000000003", date="2026-09-13T22:00:00.000Z"
    ),
]


def build(**overrides: object) -> str:
    options: dict[str, object] = {
        "language": "fr",
        "now": NOW,
        "tz": TZ,
        "stats": user_stats_from_json(load_data("user")),
        "tasks": TASKS,
        "recent_summaries": [],
    }
    options.update(overrides)
    return build_system_prompt(**options)  # type: ignore[arg-type]


def test_language_and_override_rule() -> None:
    prompt = build()

    assert "Reply in French (français)." in prompt
    assert OVERRIDE_RULE in prompt
    assert "Reply in English." in build(language="en")
    assert "Reply in English." in build(language="xx")


def test_russian_is_named_unambiguously_with_habiticas_own_words() -> None:
    """llm_calls #44, 2026-09-15: 'Reply in Russian.' alone got a Russian/Ukrainian mix
    (стрік, виконано) and a transliterated 'streak'."""
    prompt = build(language="ru")

    assert "Reply in Russian (русский)" in prompt
    assert "never Ukrainian" in prompt
    assert "no transliterated English" in prompt
    for word in ("привычка", "ежедневное дело", "задача", "серия (streak)", "начало суток"):
        assert word in prompt


def test_french_gets_habiticas_own_words_and_english_gets_none() -> None:
    french = build(language="fr")
    english = build(language="en")

    for word in ("habitude", "quotidienne", "à faire", "combo (streak)", "début de journée"):
        assert word in french
    assert "never Ukrainian" not in french
    assert "привычка" not in english and "habitude" not in english


def test_dates_are_european_and_the_model_is_told_so() -> None:
    prompt = build()

    assert "Write dates as DD.MM.YYYY (31.12.2026)" in prompt
    assert "2026-09" not in prompt  # no ISO date anywhere the model could copy from


def test_the_primer_states_the_mechanics_so_the_model_does_not_invent_them() -> None:
    """Seen live: the model claimed overdue todos cause daily damage. Only dailies do."""
    prompt = build()

    assert "How Habitica works" in prompt
    assert "Todos never cost HP, overdue or not" in prompt
    assert "every daily that was due and not completed costs HP" in prompt
    assert "this bot fires it deliberately one minute after day start" in prompt
    assert "A todo due today is overdue after midnight, not at day start" in prompt
    assert "is an action (move_task), not a preference to remember" in prompt


def test_english_content_rule_is_present() -> None:
    prompt = build()

    assert "Habitica content is always English" in prompt
    assert "translate the user's words" in prompt
    assert "match it semantically against the English task list" in prompt


def test_task_list_is_one_compact_line_per_task_with_short_ids() -> None:
    prompt = build()

    assert "- d1a2b3c4 daily · Vacuum the floors · due today, not done, streak 4" in prompt
    assert "- ab1a2b3c habit · Workout\n" in prompt
    assert "- 70d0aaaa todo · Buy milk · due 14.09.2026" in prompt
    assert "d1a2b3c4-0000" not in prompt  # never the full UUID


def test_clock_stats_and_day_start() -> None:
    prompt = build()
    user = user_stats_from_json(load_data("user"))

    assert "Now: Sunday 13.09.2026 12:00 (Europe/Zurich)." in prompt
    assert f"The Habitica day starts at {user.day_start:02d}:00." in prompt
    assert (
        f"The user's stats now: HP {user.hp:.0f}/{user.max_hp} · MP {user.mp:.0f}/{user.max_mp}"
        in prompt
    )
    assert f"level {user.lvl} mage" in prompt  # the API says "wizard"
    assert f"({user.to_next_level - user.exp:.0f} more XP to reach level {user.lvl + 1})" in prompt


def test_without_stats_or_tasks() -> None:
    prompt = build(stats=None, tasks=[])

    assert "The user's stats now" not in prompt
    assert "Habitica day starts" not in prompt
    assert "Tasks: none." in prompt


def test_recent_context_and_planning_topic() -> None:
    prompt = build(
        recent_summaries=["Discussed Latin; agreed on habit Latin vocab.", "Reviewed the week."],
        session_topic="learning Latin",
    )

    assert (
        "Recent context, most recent first:\n"
        "- Discussed Latin; agreed on habit Latin vocab.\n"
        "- Reviewed the week."
    ) in prompt
    assert prompt.endswith("This is a planning session about: learning Latin.")
    assert "Recent context" not in build()


def test_task_line_for_a_done_daily_not_due() -> None:
    line = task_line(
        known("daily", "Milk", "aaaaaaaa-0000-4000-8000-000000000009", completed=True, isDue=False),
        TZ,
    )

    assert line == "- aaaaaaaa daily · Milk · not due today, done, streak 0"


def test_due_day_is_read_in_the_users_timezone() -> None:
    """Regression: a todo the Habitica app shows as due 14/09 was announced as due 13/09."""
    letter = known(
        "todo",
        "Take letter",
        "bbbbbbbb-0000-4000-8000-000000000010",
        date="2026-09-13T22:00:00.000Z",
    )

    assert task_line(letter, TZ) == "- bbbbbbbb todo · Take letter · due 14.09.2026"
    assert task_line(letter, ZoneInfo("UTC")) == "- bbbbbbbb todo · Take letter · due 13.09.2026"
