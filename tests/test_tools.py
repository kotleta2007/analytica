"""Tool dispatch against a fake Habitica and a database seeded by one collection round."""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.collector.snapshots import collect
from app.core.replies import User
from app.core.state import get_state
from app.core.tasks import short_id
from app.core.turn import Turn, load_settings
from app.db.engine import session_scope
from app.db.models import UserSnapshot
from app.habitica.client import HabiticaError
from app.habitica.types import task_from_json, user_stats_from_json
from app.llm import tools
from app.llm.client import ToolCall
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Zurich")

DAILY = make_task("daily", "Vacuum the floors", task_id="d1a2b3c4-0000-4000-8000-000000000001")
HABIT = make_task("habit", "Workout", task_id="ab1a2b3c-0000-4000-8000-000000000002")
TODO = make_task("todo", "Buy milk", task_id="70d0aaaa-0000-4000-8000-000000000003")
DONE_TODO = make_task(
    "todo", "Old chore", task_id="0ddd0000-0000-4000-8000-000000000004", completed=True
)


@pytest.fixture
def fake_client() -> FakeHabiticaClient:
    return FakeHabiticaClient(tasks=[DAILY, HABIT, TODO], completed_todos=[DONE_TODO])


@pytest.fixture
async def seeded(db_path: Path, fake_client: FakeHabiticaClient) -> FakeHabiticaClient:
    async with session_scope() as session:
        await collect(fake_client, session, USER.id, NOW)
    fake_client.calls.clear()
    return fake_client


class TurnScope:
    """`async with TurnScope(client) as turn:` — one transaction, one Turn."""

    def __init__(self, client: FakeHabiticaClient, language: str = "en") -> None:
        self.client = client
        self.language = language

    async def __aenter__(self) -> Turn:
        self.scope = session_scope()
        session = await self.scope.__aenter__()
        settings = await load_settings(session, USER.id)
        settings.language = self.language
        return Turn(session, USER, settings, self.client, NOW, TZ)  # type: ignore[arg-type]

    async def __aexit__(self, *exc_info: object) -> None:
        await self.scope.__aexit__(*exc_info)


def call(name: str, **arguments: object) -> ToolCall:
    return ToolCall(id="call_1", name=name, arguments=dict(arguments))


# ---- reading ----


async def test_list_tasks_shows_open_tasks_with_short_ids(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("list_tasks"))

    tasks = result.content["tasks"]
    # Habits, dailies, todos — the app's column order — when tasksOrder does not list them.
    assert [(t["id"], t["type"], t["text"]) for t in tasks] == [
        ("ab1a2b3c", "habit", "Workout"),
        ("d1a2b3c4", "daily", "Vacuum the floors"),
        ("70d0aaaa", "todo", "Buy milk"),
    ]
    assert tasks[1]["due_today"] is True and tasks[1]["completed"] is False
    assert tasks[0]["counter_up"] == 0
    assert result.changed_tasks is False


async def test_tasks_follow_the_order_the_user_arranged_in_the_app(db_path: Path) -> None:
    """After a move (or a drag in the app) the model must see the new order, or it cannot
    compute "put X under Y"."""
    first = make_task("todo", "Read GAMEPLAN", task_id="aaaa0001-0000-4000-8000-000000000001")
    second = make_task("todo", "Free disk space", task_id="aaaa0002-0000-4000-8000-000000000002")
    new = make_task("todo", "Created since", task_id="aaaa0003-0000-4000-8000-000000000003")
    user = user_stats_from_json(
        {**load_data("user"), "tasksOrder": {"todos": [second.id, first.id], "dailys": [DAILY.id]}}
    )
    client = FakeHabiticaClient(user=user, tasks=[first, DAILY, second, new])
    async with session_scope() as session:
        await collect(client, session, USER.id, NOW)
    async with TurnScope(client) as turn:
        result = await tools.dispatch(turn, call("list_tasks"))

    assert [t["text"] for t in result.content["tasks"]] == [
        "Vacuum the floors",
        "Free disk space",
        "Read GAMEPLAN",
        "Created since",  # not in tasksOrder yet: after the others of its type
    ]


async def test_list_tasks_filters_by_type_and_rejects_others(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        todos = await tools.dispatch(turn, call("list_tasks", type="todo"))
        bad = await tools.dispatch(turn, call("list_tasks", type="reward"))

    assert [t["text"] for t in todos.content["tasks"]] == ["Buy milk"]
    assert bad.content == {"error": "type must be habit, daily or todo"}


async def test_list_tasks_reads_due_days_in_the_users_timezone(db_path: Path) -> None:
    """Regression: `2026-09-13T22:00Z` is due 14/09 in Zurich, and was listed as 13/09."""
    letter = make_task(
        "todo",
        "Take letter",
        task_id="1e77e400-0000-4000-8000-000000000005",
        date="2026-09-13T22:00:00.000Z",
    )
    client = FakeHabiticaClient(tasks=[letter])
    async with session_scope() as session:
        await collect(client, session, USER.id, NOW)
    async with TurnScope(client) as turn:
        result = await tools.dispatch(turn, call("list_tasks", type="todo"))

    assert result.content["tasks"][0]["due"] == "14.09.2026"


async def test_get_stats_reads_the_snapshot(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("get_stats"))

    user = seeded.user
    assert result.content["hp"] == round(user.hp, 1)
    assert result.content["lvl"] == user.lvl
    assert result.content["class"] == "mage"
    # Arithmetic is done here, not by the model.
    assert result.content["exp_remaining_to_next_level"] == round(user.to_next_level - user.exp)
    assert result.content["next_lvl"] == user.lvl + 1
    assert result.content["as_of"] == "13.09.2026 12:00"
    assert seeded.called("get_user") == []  # the snapshot is fresh


# ---- changing ----


async def test_score_task_resolves_a_short_id_and_reports_stats(
    seeded: FakeHabiticaClient,
) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("score_task", task_id="d1a2b3c4", direction="up"))

    assert seeded.called("score_task") == [(DAILY.id, "up")]
    assert result.changed_tasks is True
    assert result.content["task"] == "Vacuum the floors"
    assert result.content["result"] == "completed"
    assert result.content["stats_now"]["lvl"] == seeded.user.lvl


async def test_score_habit_counts(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("score_task", task_id=HABIT.id, direction="up"))

    assert result.content["result"] == "counted +1"


async def test_unknown_task_id_is_an_error_for_the_model(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("score_task", task_id="zzzz", direction="up"))

    assert "no task with id 'zzzz'" in result.content["error"]
    assert seeded.called("score_task") == []


async def test_create_todo_with_due_date_and_difficulty(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(
            turn,
            call(
                "create_task",
                type="todo",
                text="Buy bread",
                due_date="2026-09-14",
                difficulty="hard",
            ),
        )

    # Local midnight in UTC, the form Habitica's own UI stores, so the app shows 14/09 too.
    assert seeded.called("create_task") == [
        {"type": "todo", "text": "Buy bread", "date": "2026-09-13T22:00:00+00:00", "priority": 2.0}
    ]
    created = result.content["created"]
    assert (created["type"], created["text"], created["due"]) == ("todo", "Buy bread", "14.09.2026")
    assert result.changed_tasks is True


async def test_due_date_is_accepted_in_european_form_too(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        await tools.dispatch(
            turn, call("create_task", type="todo", text="Buy bread", due_date="14.09.2026")
        )

    assert seeded.called("create_task")[0]["date"] == "2026-09-13T22:00:00+00:00"


async def test_create_todo_with_subtasks_puts_them_in_the_checklist(
    seeded: FakeHabiticaClient,
) -> None:
    """Seen live: with no checklist parameter the model wrote the bullet points into notes."""
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(
            turn,
            call(
                "create_task",
                type="todo",
                text="Grocery shopping",
                difficulty="trivial",
                checklist=["Denner: buy coffee capsules", "Coop: buy toilet paper"],
            ),
        )

    (payload,) = seeded.called("create_task")
    assert payload["checklist"] == [
        {"text": "Denner: buy coffee capsules"},
        {"text": "Coop: buy toilet paper"},
    ]
    assert "notes" not in payload
    assert result.content["created"]["checklist"] == [
        {"text": "Denner: buy coffee capsules", "done": False},
        {"text": "Coop: buy toilet paper", "done": False},
    ]


async def test_update_replaces_the_checklist_but_keeps_ticks_on_unchanged_items(
    seeded: FakeHabiticaClient,
) -> None:
    ticked = make_task(
        "todo",
        "Grocery shopping",
        task_id="9e0c0000-0000-4000-8000-000000000006",
        checklist=[
            {"id": "item-1", "text": "Denner: buy coffee capsules", "completed": True},
            {"id": "item-2", "text": "Coop: buy toilet paper", "completed": False},
        ],
    )
    seeded.tasks.append(ticked)
    async with session_scope() as session:
        await collect(seeded, session, USER.id, NOW)
    seeded.calls.clear()

    async with TurnScope(seeded) as turn:
        await tools.dispatch(
            turn,
            call(
                "update_task",
                task_id="9e0c0000",
                checklist=["Denner: buy coffee capsules", "Migros: buy milk"],
            ),
        )

    ((_, payload),) = seeded.called("update_task")
    assert payload == {
        "checklist": [
            {"id": "item-1", "text": "Denner: buy coffee capsules", "completed": True},
            {"text": "Migros: buy milk", "completed": False},
        ]
    }


async def test_checklist_is_refused_on_habits_and_when_empty(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        habit = await tools.dispatch(
            turn, call("create_task", type="habit", text="Stretch", checklist=["x"])
        )
        empty = await tools.dispatch(
            turn, call("create_task", type="todo", text="X", checklist=["  "])
        )

    assert habit.content == {"error": "habits have no checklist"}
    assert empty.content == {"error": "checklist must have at least one item"}
    assert seeded.called("create_task") == []


@pytest.mark.parametrize(
    ("written", "stored"),
    [
        # Seen live: Unicode bullets on single lines render as one line in the app.
        (
            "• ALDI: buy coffee capsules\n• Migros: buy toilet cleaner",
            "- ALDI: buy coffee capsules\n- Migros: buy toilet cleaner",
        ),
        ("first line\nsecond line", "first line\n\nsecond line"),
        ("Intro:\n• x\n• y\nOutro", "Intro:\n\n- x\n- y\n\nOutro"),
        ("- a\n- b\n\nnote", "- a\n- b\n\nnote"),
        ("just one", "just one"),
    ],
)
def test_notes_become_markdown_the_app_renders_as_intended(written: str, stored: str) -> None:
    assert tools.markdown_notes(written) == stored


async def test_notes_are_normalised_on_the_way_to_habitica(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        await tools.dispatch(
            turn, call("update_task", task_id="70d0aaaa", notes="• skimmed\n• milk 2%")
        )

    ((_, payload),) = seeded.called("update_task")
    assert payload == {"notes": "- skimmed\n- milk 2%"}


async def test_create_habit_is_positive_only_by_default(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        await tools.dispatch(turn, call("create_task", type="habit", text="Stretch"))

    assert seeded.called("create_task") == [
        {"type": "habit", "text": "Stretch", "up": True, "down": False}
    ]


async def test_create_daily_on_chosen_weekdays(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        await tools.dispatch(
            turn,
            call("create_task", type="daily", text="Lift", repeat_days=["Mon", "wednesday", "fri"]),
        )

    (payload,) = seeded.called("create_task")
    assert payload["frequency"] == "weekly"
    assert {k for k, on in payload["repeat"].items() if on} == {"m", "w", "f"}


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ({"type": "daily", "text": "X", "due_date": "2026-09-14"}, "only todos have a due date"),
        ({"type": "todo", "text": "X", "due_date": "tomorrow"}, "due_date must be DD.MM.YYYY"),
        ({"type": "todo", "text": "X", "difficulty": "brutal"}, "difficulty must be"),
        ({"type": "todo", "text": "X", "repeat_days": ["mon"]}, "only dailies have repeat_days"),
        ({"type": "todo"}, "text is required"),
        ({"type": "reward", "text": "X"}, "type must be"),
    ],
)
async def test_create_task_rejects_bad_arguments(
    seeded: FakeHabiticaClient, arguments: dict[str, object], message: str
) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("create_task", **arguments))

    assert message in result.content["error"]
    assert seeded.called("create_task") == []


async def test_update_task_sends_only_the_given_fields(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(
            turn, call("update_task", task_id="70d0aaaa", notes="skimmed", due_date="2026-09-20")
        )
        empty = await tools.dispatch(turn, call("update_task", task_id="70d0aaaa"))

    assert seeded.called("update_task") == [
        (TODO.id, {"notes": "skimmed", "date": "2026-09-19T22:00:00+00:00"})
    ]
    assert result.content["updated"]["notes"] == "skimmed"
    assert empty.content["error"].startswith("nothing to update")
    assert "checklist" in empty.content["error"]


async def test_move_task_to_the_top_and_bottom(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        top = await tools.dispatch(turn, call("move_task", task_id="70d0aaaa", position="top"))
        bottom = await tools.dispatch(
            turn, call("move_task", task_id="70d0aaaa", position="bottom")
        )
        third = await tools.dispatch(turn, call("move_task", task_id="d1a2b3c4", position=3))

    assert seeded.called("move_task") == [(TODO.id, 0), (TODO.id, -1), (DAILY.id, 2)]
    assert top.content == {"moved": "Buy milk", "to": "top of the todo list", "list_length": 1}
    assert bottom.content["to"] == "bottom of the todo list"
    assert third.content["to"] == "position 3 of the daily list"
    assert top.changed_tasks is False  # a move is remembered, not collected


async def test_a_move_updates_our_order_without_a_snapshot(db_path: Path) -> None:
    first = make_task("todo", "Read GAMEPLAN", task_id="aaaa0001-0000-4000-8000-000000000001")
    second = make_task("todo", "Free disk space", task_id="aaaa0002-0000-4000-8000-000000000002")
    client = FakeHabiticaClient(tasks=[first, second])
    async with session_scope() as session:
        await collect(client, session, USER.id, NOW)
    client.calls.clear()

    async with TurnScope(client) as turn:
        await tools.dispatch(turn, call("move_task", task_id="aaaa0002", position="top"))
        listed = await tools.dispatch(turn, call("list_tasks"))

    assert [t["text"] for t in listed.content["tasks"]] == ["Free disk space", "Read GAMEPLAN"]
    assert [name for name, _ in client.calls] == ["move_task"]  # no get_user / get_tasks
    async with session_scope() as session:
        snapshots = await session.scalar(select(func.count()).select_from(UserSnapshot))
    assert snapshots == 1  # the seed round only


@pytest.mark.parametrize("position", ["middle", 0, -2, True, "x"])
async def test_move_task_rejects_bad_positions(
    seeded: FakeHabiticaClient, position: object
) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(
            turn, call("move_task", task_id="70d0aaaa", position=position)
        )

    assert result.content["error"].startswith("position must be top, bottom, or a slot number")
    assert seeded.called("move_task") == []


# ---- confirmation ----


async def test_delete_task_asks_for_confirmation_and_parks_the_action(
    seeded: FakeHabiticaClient,
) -> None:
    async with TurnScope(seeded, language="fr") as turn:
        result = await tools.dispatch(turn, call("delete_task", task_id="ab1a2b3c"))
        pending = await tools.pending_action(turn)

    assert seeded.called("delete_task") == []
    assert result.changed_tasks is False
    assert result.content["status"] == "needs_confirmation"
    assert result.content["action"] == "delete “Workout”"
    assert [b.label for b in result.buttons] == ["✅ Vas-y", "❌ Annuler"]
    assert [b.action for b in result.buttons] == ["confirm", "cancel"]
    token = result.buttons[0].payload["token"]
    assert result.buttons[1].payload == {"token": token}
    assert pending is not None
    assert pending["actions"][0]["tool"] == "delete_task"
    assert pending["actions"][0]["arguments"] == {"task_id": HABIT.id}
    assert pending["token"] == token
    assert tools.describe_pending(pending, "fr") == "supprimer « Workout »"


async def test_run_action_deletes_for_real(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        await tools.dispatch(turn, call("delete_task", task_id="ab1a2b3c"))
        pending = await tools.pending_action(turn)
        assert pending is not None
        result = await tools.run_action(turn, pending["actions"][0])

    assert seeded.called("delete_task") == [HABIT.id]
    assert result.content == {"deleted": "Workout"}
    assert result.changed_tasks is True


async def test_delete_of_unknown_task_parks_nothing(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("delete_task", task_id="nope"))
        assert await get_state(turn.session, USER.id) is None

    assert "no task with id" in result.content["error"]


# ---- failures ----


async def test_unknown_tool_and_bad_json_are_reported_to_the_model(
    seeded: FakeHabiticaClient,
) -> None:
    async with TurnScope(seeded) as turn:
        unknown = await tools.dispatch(turn, call("fly"))
        broken = await tools.dispatch(
            turn, ToolCall("c", "get_stats", {"_unparsable_arguments": "{x"})
        )

    assert unknown.content == {"error": "unknown tool 'fly'"}
    assert "not valid JSON" in broken.content["error"]


async def test_habitica_refusal_becomes_an_error_result(seeded: FakeHabiticaClient) -> None:
    async def refuse(task_id: str, direction: str) -> None:
        raise HabiticaError(404, "NotFound", "Task not found.")

    seeded.score_task = refuse  # type: ignore[method-assign]
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call("score_task", task_id="d1a2b3c4", direction="up"))

    assert result.content == {"error": "Habitica refused: Task not found."}


# ---- schemas ----


def test_every_tool_has_a_valid_function_schema() -> None:
    names = [schema["function"]["name"] for schema in tools.schemas()]

    assert names == [
        "list_tasks",
        "score_task",
        "create_task",
        "update_task",
        "delete_task",
        "cast_skill",
        "move_task",
        "get_stats",
        "add_glossary_term",
    ]
    for schema in tools.schemas():
        assert schema["type"] == "function"
        assert schema["function"]["parameters"]["type"] == "object"
        assert "properties" in schema["function"]["parameters"]


def test_short_id_is_the_first_eight_characters() -> None:
    assert short_id(DAILY.id) == "d1a2b3c4"


def test_fixture_tasks_have_no_short_id_collisions() -> None:
    ids = [task_from_json(item).id for item in load_data("tasks")]

    assert len({short_id(i) for i in ids}) == len(ids)


async def test_an_action_parked_in_the_old_flat_shape_is_still_pressable(
    seeded: FakeHabiticaClient,
) -> None:
    from app.core.state import set_state

    flat = {
        "tool": "delete_task",
        "arguments": {"task_id": HABIT.id},
        "describe": {"key": "action_delete_task", "values": {"text": "Workout"}},
        "token": "0123456789ab",
    }
    async with TurnScope(seeded) as turn:
        await set_state(turn.session, USER.id, tools.PENDING_CONFIRM, flat, turn.now)
        pending = await tools.pending_action(turn)

    assert pending == {
        "actions": [{k: flat[k] for k in ("tool", "arguments", "describe")}],
        "token": "0123456789ab",
    }
