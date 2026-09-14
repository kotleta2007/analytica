from datetime import UTC, datetime

from app.habitica.types import (
    GEAR_STATS,
    Task,
    gear_from_content,
    score_result_from_json,
    task_from_json,
    user_stats_from_json,
    webhook_from_json,
)
from tests.habitica_fixtures import load_data


def tasks_by_text() -> dict[str, Task]:
    return {task.text: task for task in map(task_from_json, load_data("tasks"))}


def test_user_stats_including_day_start_and_offset() -> None:
    user = user_stats_from_json(load_data("user"))

    assert (user.hp, user.mp, user.exp, user.gp) == (49.0, 242.0, 2931.0, 4329.0)
    assert user.lvl == 120
    assert user.class_name == "wizard"
    assert user.to_next_level == 4940
    assert (user.max_hp, user.max_mp) == (50, 438)
    assert user.day_start == 11
    assert user.timezone_offset == -120
    assert user.last_cron == datetime(2026, 9, 11, 10, 7, 31, 619000, tzinfo=UTC)
    assert user.needs_cron is False
    assert user.equipped["weapon"] == "weapon_special_2"
    assert len(user.owned_gear) == 119
    assert user.raw["preferences"]["dayStart"] == 11


def test_daily_with_history() -> None:
    daily = tasks_by_text()["Morning supplements"]

    assert daily.type == "daily"
    assert daily.completed is True
    assert daily.is_due is True
    assert daily.streak == 29
    assert daily.frequency == "daily"
    assert daily.every_x == 1
    assert daily.repeat == dict.fromkeys(("m", "t", "w", "th", "f", "s", "su"), True)
    assert daily.start_date == datetime(2026, 8, 12, 22, 0, tzinfo=UTC)
    assert daily.created_at == datetime(2026, 8, 13, 12, 9, 39, 634000, tzinfo=UTC)
    assert len(daily.history) == 29
    first = daily.history[0]
    assert first.at == datetime(2026, 8, 13, 12, 28, 6, 138000, tzinfo=UTC)
    assert (first.is_due, first.completed) == (True, True)
    assert first.scored_up is None
    # habit-only fields stay unset on a daily
    assert daily.counter_up is None
    assert daily.due_date is None


def test_habit_with_history_and_counters() -> None:
    habit = tasks_by_text()["Complete a :tomato:!"]

    assert habit.type == "habit"
    assert (habit.up, habit.down) == (True, False)
    assert (habit.counter_up, habit.counter_down) == (6, 0)
    assert habit.completed is None
    assert habit.streak is None
    assert len(habit.history) == 13
    last = habit.history[-1]
    assert (last.scored_up, last.scored_down) == (1, 0)
    assert last.is_due is None


def test_todo_with_due_date() -> None:
    todo = tasks_by_text()["Refresh knowledge on how Habitica works"]

    assert todo.type == "todo"
    assert todo.completed is False
    assert todo.due_date == datetime(2026, 9, 5, 22, 0, tzinfo=UTC)
    assert todo.date_completed is None
    assert todo.checklist == []
    assert todo.checklist_done == 0


def test_completed_todo_keeps_its_completion_time() -> None:
    todo = task_from_json(load_data("completed_todos")[0])

    assert todo.completed is True
    assert todo.date_completed == datetime(2026, 9, 10, 19, 55, 53, 755000, tzinfo=UTC)


def test_checklist_items_are_parsed() -> None:
    data = {
        "id": "t1",
        "type": "todo",
        "text": "Essay",
        "createdAt": "2026-09-01T10:00:00.000Z",
        "updatedAt": "2026-09-02T10:00:00.000Z",
        "completed": False,
        "checklist": [
            {"id": "c1", "text": "outline", "completed": True},
            {"id": "c2", "text": "draft", "completed": False},
        ],
    }

    todo = task_from_json(data)

    assert [item.text for item in todo.checklist] == ["outline", "draft"]
    assert todo.checklist_done == 1
    assert todo.notes == ""


def test_gear_from_content() -> None:
    gear = gear_from_content(load_data("content"))

    weapon = gear["weapon_special_2"]
    assert weapon.slot == "weapon"
    assert weapon.klass == "special"
    assert weapon.stats == {"str": 25, "int": 0, "per": 25, "con": 0}
    assert set(weapon.stats) == set(GEAR_STATS)
    assert gear["armor_special_snowSovereignRobes"].stats["per"] == 17


def test_score_result() -> None:
    result = score_result_from_json(
        {"delta": 1.5, "hp": 49, "mp": 240, "exp": 2950, "gp": 4330.2, "lvl": 120, "_tmp": {}}
    )

    assert result.delta == 1.5
    assert result.gp == 4330.2
    assert result.lvl == 120


def test_webhook() -> None:
    hook = webhook_from_json(
        {
            "id": "w1",
            "url": "https://bot.example.com/webhooks/habitica/s",
            "type": "taskActivity",
            "enabled": True,
            "options": {"scored": True, "created": False},
        }
    )

    assert hook.id == "w1"
    assert hook.type == "taskActivity"
    assert hook.options == {"scored": True, "created": False}


def test_exp_remaining_is_computed_and_never_negative() -> None:
    from dataclasses import replace

    from app.habitica.types import class_label

    user = user_stats_from_json(load_data("user"))

    assert user.exp_remaining == round(user.to_next_level - user.exp)
    assert replace(user, exp=user.to_next_level + 5.0).exp_remaining == 0
    assert class_label("wizard") == "mage" and class_label("rogue") == "rogue"
