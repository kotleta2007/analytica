from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select

from app.collector.snapshots import collect, snapshot_tasks, snapshot_user
from app.db.engine import session_scope
from app.db.models import TaskEvent, TaskRow, TaskSnapshot, UserSnapshot
from app.habitica.types import user_stats_from_json
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task, with_fields

USER = "00000000-0000-4000-8000-000000000001"
T0 = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
T1 = T0 + timedelta(minutes=30)


async def run_snapshot(client: FakeHabiticaClient, at: datetime, **kwargs: bool) -> None:
    async with session_scope() as session:
        await snapshot_tasks(client, session, USER, at, **kwargs)


async def events_by_kind() -> list[tuple[str, str | None, str]]:
    """(kind, direction, task text) for every event, in insertion order."""
    async with session_scope() as session:
        rows = await session.execute(
            select(TaskEvent, TaskRow.text)
            .join(TaskRow, TaskRow.id == TaskEvent.task_id)
            .order_by(TaskEvent.id)
        )
        return [(event.kind, event.direction, text) for event, text in rows]


async def test_user_snapshot_keeps_the_game_state_only(db_path: Path) -> None:
    client = FakeHabiticaClient()

    async with session_scope() as session:
        await snapshot_user(client, session, USER, T0)

    async with session_scope() as session:
        snapshot = await session.scalar(select(UserSnapshot))

    assert snapshot is not None
    assert (snapshot.hp, snapshot.mp, snapshot.lvl, snapshot.class_name) == (49, 242, 120, "wizard")
    assert snapshot.raw["preferences"]["dayStart"] == 11
    assert snapshot.raw["lastCron"] == "2026-09-11T10:07:31.619Z"
    assert set(snapshot.raw["items"]) == {"gear"}
    assert "inbox" not in snapshot.raw


async def test_first_snapshot_records_every_task_as_created(db_path: Path) -> None:
    client = FakeHabiticaClient(tasks=[make_task("daily", "Floss"), make_task("habit", "Run")])

    await run_snapshot(client, T0)

    async with session_scope() as session:
        rows = list(await session.scalars(select(TaskRow).order_by(TaskRow.id)))
        snapshots = list(await session.scalars(select(TaskSnapshot)))
    assert [(row.habitica_id, row.type, row.first_seen_at) for row in rows] == [
        ("daily-floss", "daily", T0),
        ("habit-run", "habit", T0),
    ]
    assert len(snapshots) == 2
    events = await events_by_kind()
    assert events == [("created", None, "Floss"), ("created", None, "Run")]


async def test_diff_produces_the_expected_events(db_path: Path) -> None:
    floss = make_task("daily", "Floss")
    read = make_task("daily", "Read", completed=True, streak=4)
    run = make_task("habit", "Run", counterUp=1, value=1.0)
    essay = make_task("todo", "Essay")
    old = make_task("todo", "Old todo")
    client = FakeHabiticaClient(tasks=[floss, read, run, essay, old])
    await run_snapshot(client, T0)

    client.tasks = [
        with_fields(floss, completed=True, streak=1, value=1.0),  # completed
        with_fields(read, completed=False, streak=0),  # uncompleted by hand
        with_fields(run, counterUp=3, value=2.9),  # two ups
        with_fields(essay, text="Essay draft"),  # renamed
        make_task("habit", "Stretch"),  # new
        # "Old todo" is gone
    ]
    await run_snapshot(client, T1)

    events = await events_by_kind()
    assert events[:2] == [
        ("created", None, "Floss"),
        ("created", None, "Read"),
    ]
    assert events[5:] == [
        ("completed", None, "Floss"),
        ("uncompleted", None, "Read"),
        ("scored", "up", "Run"),
        ("scored", "up", "Run"),
        ("updated", None, "Essay draft"),
        ("created", None, "Stretch"),
        ("deleted", None, "Old todo"),
    ]
    async with session_scope() as session:
        deleted = await session.scalar(
            select(TaskRow).where(TaskRow.habitica_id == "todo-old-todo")
        )
        renamed = await session.scalar(select(TaskRow).where(TaskRow.habitica_id == "todo-essay"))
        scored = list(
            await session.scalars(
                select(TaskEvent).where(TaskEvent.kind == "scored").order_by(TaskEvent.id)
            )
        )
    assert deleted is not None and deleted.deleted_at == T1
    assert renamed is not None and renamed.text == "Essay draft"
    assert renamed.raw["text"] == "Essay draft"
    assert [round(event.delta or 0, 2) for event in scored] == [0.95, 0.95]


async def test_habit_score_falls_back_to_value_when_counters_reset(db_path: Path) -> None:
    run = make_task("habit", "Run", counterUp=5, value=4.0)
    client = FakeHabiticaClient(tasks=[run])
    await run_snapshot(client, T0)

    client.tasks = [with_fields(run, counterUp=0, value=4.8)]
    await run_snapshot(client, T1)

    events = await events_by_kind()
    assert events[-1] == ("scored", "up", "Run")


async def test_unchanged_tasks_do_not_get_new_snapshot_rows(db_path: Path) -> None:
    client = FakeHabiticaClient(tasks=[make_task("daily", "Floss")])
    await run_snapshot(client, T0)
    await run_snapshot(client, T1)

    async with session_scope() as session:
        snapshots = list(await session.scalars(select(TaskSnapshot)))
        row = await session.scalar(select(TaskRow))
    assert len(snapshots) == 1
    assert row is not None and row.last_seen_at == T1
    assert await events_by_kind() == [("created", None, "Floss")]


async def test_checklist_progress_is_an_event(db_path: Path) -> None:
    essay = make_task(
        "todo",
        "Essay",
        checklist=[
            {"id": "c1", "text": "outline", "completed": False},
            {"id": "c2", "text": "draft", "completed": False},
        ],
    )
    client = FakeHabiticaClient(tasks=[essay])
    await run_snapshot(client, T0)
    client.tasks = [
        with_fields(
            essay,
            checklist=[
                {**item, "completed": item["id"] == "c1"} for item in essay.raw["checklist"]
            ],
        )
    ]
    await run_snapshot(client, T1)

    async with session_scope() as session:
        event = await session.scalar(select(TaskEvent).where(TaskEvent.kind == "checklist"))
    assert event is not None
    assert event.raw == {"done": 1, "total": 2, "was": 0}


async def test_completed_todos_are_not_mistaken_for_deleted(db_path: Path) -> None:
    essay = make_task("todo", "Essay")
    client = FakeHabiticaClient(tasks=[essay])
    await run_snapshot(client, T0)

    client.tasks = []
    client.completed_todos = [with_fields(essay, completed=True, dateCompleted=T1.isoformat())]
    await run_snapshot(client, T1)

    assert await events_by_kind() == [("created", None, "Essay"), ("completed", None, "Essay")]


async def test_daily_reset_by_cron_is_not_an_uncomplete(db_path: Path) -> None:
    floss = make_task("daily", "Floss", completed=True, streak=3)
    client = FakeHabiticaClient(tasks=[floss])
    await run_snapshot(client, T0)

    client.tasks = [with_fields(floss, completed=False)]
    await run_snapshot(client, T1, reset_by_cron=True)

    assert await events_by_kind() == [("created", None, "Floss")]


async def test_collect_detects_cron_from_the_user_snapshot(db_path: Path) -> None:
    floss = make_task("daily", "Floss", completed=True, streak=3)
    client = FakeHabiticaClient(tasks=[floss])
    async with session_scope() as session:
        await collect(client, session, USER, T0)

    # cron moved, the daily was reset
    user_raw = {**load_data("user"), "lastCron": "2026-09-12T09:00:01.000Z"}
    client.user = user_stats_from_json(user_raw)
    client.tasks = [with_fields(floss, completed=False)]
    async with session_scope() as session:
        outcome = await collect(client, session, USER, T1)

    assert [event.kind for event in outcome.events] == []
    assert await events_by_kind() == [("created", None, "Floss")]

    # no cron this time, so the same transition is the user un-ticking
    client.tasks = [with_fields(floss, completed=True)]
    async with session_scope() as session:
        await collect(client, session, USER, T1 + timedelta(minutes=30))
    client.tasks = [with_fields(floss, completed=False)]
    async with session_scope() as session:
        outcome = await collect(client, session, USER, T1 + timedelta(minutes=60))

    assert [event.kind for event in outcome.events] == ["uncompleted"]
