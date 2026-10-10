from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.collector.day_close import DayConfig, cron_hold, set_cron_hold
from app.core import outbound
from app.core.replies import Reply, User
from app.db.engine import session_scope
from app.db.models import DailyRecord, TaskEvent, UserSnapshot
from app.habitica.types import task_from_json, user_stats_from_json
from app.jobs import scheduler as scheduler_module
from app.jobs.scheduler import (
    ASK_JOB,
    CLOSE_DAY_JOB,
    RUN_CRON_JOB,
    SNAPSHOT_JOB,
    CollectorJobs,
    day_job_times,
)
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task

USER = "00000000-0000-4000-8000-000000000001"
SUMMER = DayConfig(day_start=11, timezone_offset=-120)


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_day_jobs_bracket_the_next_day_start() -> None:
    times = day_job_times(SUMMER, utc(2026, 9, 11, 15, 0))

    assert times.ask == utc(2026, 9, 12, 8, 30)
    assert times.close == utc(2026, 9, 12, 8, 58)
    assert times.cron == utc(2026, 9, 12, 9, 1)


def test_just_before_day_start_ask_and_close_are_tomorrow_and_cron_is_today() -> None:
    times = day_job_times(SUMMER, utc(2026, 9, 12, 8, 59))

    assert times.ask == utc(2026, 9, 13, 8, 30)
    assert times.close == utc(2026, 9, 13, 8, 58)
    assert times.cron == utc(2026, 9, 12, 9, 1)


def test_right_after_our_cron_all_jobs_move_to_the_next_day() -> None:
    times = day_job_times(SUMMER, utc(2026, 9, 12, 9, 1, 30))

    assert times.ask == utc(2026, 9, 13, 8, 30)
    assert times.close == utc(2026, 9, 13, 8, 58)
    assert times.cron == utc(2026, 9, 13, 9, 1)


def test_day_jobs_follow_a_dst_change() -> None:
    winter = DayConfig(day_start=11, timezone_offset=-60)

    times = day_job_times(winter, utc(2026, 10, 25, 12, 0))

    assert times.ask == utc(2026, 10, 26, 9, 30)
    assert times.close == utc(2026, 10, 26, 9, 58)
    assert times.cron == utc(2026, 10, 26, 10, 1)


def test_while_the_question_is_open_cron_waits_at_the_hold_not_tomorrow() -> None:
    hold = utc(2026, 9, 12, 10, 30)  # dayStart + 90 min

    before_start = day_job_times(SUMMER, utc(2026, 9, 12, 8, 40), hold)
    after_start = day_job_times(SUMMER, utc(2026, 9, 12, 9, 30), hold, needs_cron=True)
    hold_over = day_job_times(SUMMER, utc(2026, 9, 12, 10, 31), hold, needs_cron=False)

    assert before_start.cron == utc(2026, 9, 12, 9, 1)  # the scheduled time comes first
    assert after_start.cron == hold  # not 13.09. 09:01
    assert hold_over.cron == utc(2026, 9, 13, 9, 1)


def test_an_account_waiting_for_cron_with_nothing_holding_it_gets_the_job_at_once() -> None:
    """A restart after day start: run_cron_job decides (and asks first if nobody did)."""
    answered = utc(2026, 9, 12, 8, 45)

    times = day_job_times(SUMMER, utc(2026, 9, 12, 9, 30), answered, needs_cron=True)
    never_asked = day_job_times(SUMMER, utc(2026, 9, 12, 9, 30), None, needs_cron=True)

    assert times.cron == utc(2026, 9, 12, 9, 30, 5)
    assert never_asked.cron == utc(2026, 9, 12, 9, 30, 5)


@pytest.fixture
async def jobs(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CollectorJobs]:
    client = FakeHabiticaClient(tasks=[task_from_json(item) for item in load_data("tasks")])
    monkeypatch.setattr(scheduler_module, "now", lambda: utc(2026, 9, 11, 15, 0))
    # Started but paused: jobs are real (so replace_existing works) and never fire.
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.start(paused=True)
    jobs = CollectorJobs(client=client, user_id=USER, scheduler=scheduler)
    yield jobs
    if scheduler.running:
        scheduler.shutdown(wait=False)


async def test_snapshot_job_collects_and_plans_the_day_jobs(jobs: CollectorJobs) -> None:
    await jobs.snapshot_job()

    async with session_scope() as session:
        assert await session.scalar(select(func.count(UserSnapshot.id))) == 1
    close_job = jobs.scheduler.get_job(CLOSE_DAY_JOB)
    cron_job = jobs.scheduler.get_job(RUN_CRON_JOB)
    assert close_job is not None and close_job.trigger.run_date == utc(2026, 9, 12, 8, 58)
    assert cron_job is not None and cron_job.trigger.run_date == utc(2026, 9, 12, 9, 1)


async def test_start_collects_backfills_and_schedules(jobs: CollectorJobs) -> None:
    await jobs.start()
    try:
        assert jobs.scheduler.running
        assert jobs.scheduler.get_job(SNAPSHOT_JOB) is not None
        assert jobs.scheduler.get_job(CLOSE_DAY_JOB) is not None
        async with session_scope() as session:
            records = await session.scalar(select(func.count(DailyRecord.id)))
            backfilled = await session.scalar(
                select(func.count(TaskEvent.id)).where(TaskEvent.source == "backfill")
            )
        assert records and backfilled
    finally:
        await jobs.stop()
    assert not jobs.scheduler.running


async def test_start_survives_a_failing_first_collection(
    jobs: CollectorJobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom() -> None:
        raise RuntimeError("habitica is down")

    monkeypatch.setattr(jobs, "snapshot_job", boom)

    await jobs.start()
    try:
        assert jobs.scheduler.running
        assert jobs.scheduler.get_job(SNAPSHOT_JOB) is not None
    finally:
        await jobs.stop()


async def test_close_day_and_cron_jobs_run_and_replan(
    jobs: CollectorJobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = jobs.client
    assert isinstance(client, FakeHabiticaClient)
    client.tasks = [make_task("daily", "Floss", completed=True)]

    monkeypatch.setattr(scheduler_module, "now", lambda: utc(2026, 9, 12, 8, 58))
    await jobs.close_day_job()
    monkeypatch.setattr(scheduler_module, "now", lambda: utc(2026, 9, 12, 9, 1))
    client.user = user_stats_from_json({**load_data("user"), "needsCron": True})
    await jobs.run_cron_job()

    assert client.called("run_cron") == [None]
    async with session_scope() as session:
        record = await session.scalar(select(DailyRecord))
    assert record is not None and record.day == utc(2026, 9, 11).date() and record.completed
    cron_job = jobs.scheduler.get_job(RUN_CRON_JOB)
    assert cron_job is not None and cron_job.trigger.run_date == utc(2026, 9, 13, 9, 1)
    assert timedelta(days=1) == cron_job.trigger.run_date - utc(2026, 9, 12, 9, 1)


# ---- the day-turn window ----


@pytest.fixture
def delivered() -> Iterator[list[Reply]]:
    """An adapter that records what the jobs send."""
    replies: list[Reply] = []

    async def hook(user: User, reply: Reply) -> None:
        replies.append(reply)

    outbound.register(hook)
    yield replies
    outbound.unregister()


def unticked_milk(client: FakeHabiticaClient) -> None:
    client.tasks = [
        make_task("daily", "Glass of milk", completed=False, isDue=True),
        make_task("daily", "Floss", completed=True, isDue=True),
    ]


def at(*args: int) -> datetime:
    return utc(*args)


async def hold() -> datetime | None:
    async with session_scope() as session:
        return await cron_hold(session, USER)


def run_date(jobs: CollectorJobs, job_id: str) -> datetime:
    job = jobs.scheduler.get_job(job_id)
    assert job is not None
    return job.trigger.run_date


async def test_ask_job_sends_the_question_and_holds_cron_until_the_grace_deadline(
    jobs: CollectorJobs, delivered: list[Reply], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = jobs.client
    assert isinstance(client, FakeHabiticaClient)
    unticked_milk(client)
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 8, 30))

    await jobs.ask_unticked_job()

    (reply,) = delivered
    assert "• Glass of milk" in reply.text and "Floss" not in reply.text
    assert [b.label for b in reply.buttons] == ["✅ Glass of milk", "Nothing else"]
    assert await hold() == at(2026, 9, 12, 10, 30)
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 12, 9, 1)  # the schedule comes first
    assert run_date(jobs, ASK_JOB) == at(2026, 9, 13, 8, 30)


async def test_ask_job_with_nothing_unticked_sends_nothing_and_cron_runs_on_schedule(
    jobs: CollectorJobs, delivered: list[Reply], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = jobs.client
    assert isinstance(client, FakeHabiticaClient)
    client.tasks = [make_task("daily", "Floss", completed=True, isDue=True)]
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 8, 30))

    await jobs.ask_unticked_job()

    assert delivered == []
    assert await hold() == at(2026, 9, 12, 8, 30)  # answered, as far as the schedule knows
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 12, 9, 1)


async def test_ask_job_without_an_adapter_does_not_hold_cron(
    jobs: CollectorJobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nobody could answer; waiting would only delay the reset."""
    client = jobs.client
    assert isinstance(client, FakeHabiticaClient)
    unticked_milk(client)
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 8, 30))

    await jobs.ask_unticked_job()

    assert await hold() == at(2026, 9, 12, 8, 30)
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 12, 9, 1)


async def test_cron_waits_at_the_hold_through_the_replans_then_turns_the_day(
    jobs: CollectorJobs, delivered: list[Reply], monkeypatch: pytest.MonkeyPatch
) -> None:
    client = jobs.client
    assert isinstance(client, FakeHabiticaClient)
    unticked_milk(client)
    async with session_scope() as session:
        await set_cron_hold(session, USER, at(2026, 9, 12, 10, 30))  # asked at 10:30 Zurich

    # 11:01: the question is open, so no cron; the job re-arms at the hold.
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 9, 1))
    await jobs.run_cron_job()
    assert client.called("run_cron") == []
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 12, 10, 30)

    # 11:30: the half-hourly snapshot sees an account waiting for cron and keeps the hold.
    client.user = user_stats_from_json({**load_data("user"), "needsCron": True})
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 9, 30))
    await jobs.snapshot_job()
    assert client.called("run_cron") == []
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 12, 10, 30)

    # 12:30: the wait is over — the ending day (11.09.) is closed as it stands, then cron.
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 10, 30))
    await jobs.run_cron_job()
    assert client.called("run_cron") == [None]
    async with session_scope() as session:
        records = list(await session.scalars(select(DailyRecord)))
    assert {(r.day, r.completed) for r in records} == {
        (utc(2026, 9, 11).date(), False),
        (utc(2026, 9, 11).date(), True),
    }
    assert await hold() is None
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 13, 9, 1)
    assert delivered == []


async def test_run_cron_job_asks_first_when_nobody_did(
    jobs: CollectorJobs, delivered: list[Reply], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process was down at dayStart − 30: the question is put now, with its full grace."""
    client = jobs.client
    assert isinstance(client, FakeHabiticaClient)
    unticked_milk(client)
    client.user = user_stats_from_json({**load_data("user"), "needsCron": True})
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 9, 40))

    await jobs.run_cron_job()

    (reply,) = delivered
    assert "• Glass of milk" in reply.text
    assert client.called("run_cron") == []
    assert await hold() == at(2026, 9, 12, 11, 10)  # 90 min from the late question
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 12, 11, 10)


async def test_the_job_left_at_the_hold_after_the_buttons_turned_the_day_asks_nothing(
    jobs: CollectorJobs, delivered: list[Reply], monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Nothing else* at 12:20 turned the day and cleared the hold; no snapshot replanned
    before 12:30, so the job armed for the hold still fires — into the new day."""
    client = jobs.client
    assert isinstance(client, FakeHabiticaClient)
    unticked_milk(client)  # the new day's dailies, reset by the cron that just ran
    monkeypatch.setattr(scheduler_module, "now", lambda: at(2026, 9, 12, 10, 30))

    await jobs.run_cron_job()

    assert delivered == []
    assert client.called("run_cron") == []
    assert await hold() is None
    assert run_date(jobs, RUN_CRON_JOB) == at(2026, 9, 13, 9, 1)
