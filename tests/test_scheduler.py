from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select

from app.collector.day_close import DayConfig
from app.db.engine import session_scope
from app.db.models import DailyRecord, TaskEvent, UserSnapshot
from app.habitica.types import task_from_json
from app.jobs import scheduler as scheduler_module
from app.jobs.scheduler import (
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
    close_at, cron_at = day_job_times(SUMMER, utc(2026, 9, 11, 15, 0))

    assert close_at == utc(2026, 9, 12, 8, 58)
    assert cron_at == utc(2026, 9, 12, 9, 1)


def test_just_before_day_start_the_close_is_tomorrow_and_cron_is_today() -> None:
    close_at, cron_at = day_job_times(SUMMER, utc(2026, 9, 12, 8, 59))

    assert close_at == utc(2026, 9, 13, 8, 58)
    assert cron_at == utc(2026, 9, 12, 9, 1)


def test_right_after_our_cron_both_jobs_move_to_the_next_day() -> None:
    close_at, cron_at = day_job_times(SUMMER, utc(2026, 9, 12, 9, 1, 30))

    assert close_at == utc(2026, 9, 13, 8, 58)
    assert cron_at == utc(2026, 9, 13, 9, 1)


def test_day_jobs_follow_a_dst_change() -> None:
    winter = DayConfig(day_start=11, timezone_offset=-60)

    close_at, cron_at = day_job_times(winter, utc(2026, 10, 25, 12, 0))

    assert close_at == utc(2026, 10, 26, 9, 58)
    assert cron_at == utc(2026, 10, 26, 10, 1)


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
    await jobs.run_cron_job()

    assert client.called("run_cron") == [None]
    async with session_scope() as session:
        record = await session.scalar(select(DailyRecord))
    assert record is not None and record.day == utc(2026, 9, 11).date() and record.completed
    cron_job = jobs.scheduler.get_job(RUN_CRON_JOB)
    assert cron_job is not None and cron_job.trigger.run_date == utc(2026, 9, 13, 9, 1)
    assert timedelta(days=1) == cron_job.trigger.run_date - utc(2026, 9, 12, 9, 1)
