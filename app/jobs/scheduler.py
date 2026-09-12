"""APScheduler wiring for the collector.

    every 30 min        snapshot_job:   collect(), then re-plan the day jobs
    dayStart − 2 min    close_day_job:  close_day()
    dayStart + 1 min    run_cron_job:   run_cron()

The two day jobs are one-shot `date` triggers, re-planned after every
collection round from the `dayStart` and `timezoneOffset` in the freshest
`/user` response — so a DST change or a changed day start is picked up
within half an hour, without configuration.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.collector.backfill import backfill
from app.collector.day_close import DayConfig, close_day, run_cron
from app.collector.snapshots import collect, latest_user_snapshot
from app.db.engine import session_scope
from app.habitica.client import HabiticaClient

logger = logging.getLogger(__name__)

SNAPSHOT_EVERY = timedelta(minutes=30)
CLOSE_DAY_BEFORE = timedelta(minutes=2)
RUN_CRON_AFTER = timedelta(minutes=1)

SNAPSHOT_JOB = "snapshot"
CLOSE_DAY_JOB = "close_day"
RUN_CRON_JOB = "run_cron"


@dataclass
class CollectorJobs:
    client: HabiticaClient
    user_id: str
    scheduler: AsyncIOScheduler

    async def start(self) -> None:
        """First collection (and backfill) now, then the schedule."""
        try:
            await self.snapshot_job()
            async with session_scope() as session:
                await backfill(self.client, session, self.user_id)
        except Exception:
            logger.exception("initial collection failed; the schedule will retry")
        self.scheduler.add_job(
            self.snapshot_job,
            "interval",
            seconds=SNAPSHOT_EVERY.total_seconds(),
            id=SNAPSHOT_JOB,
            replace_existing=True,
        )
        if not self.scheduler.running:
            self.scheduler.start()
        logger.info("collector scheduled: snapshots every %s", SNAPSHOT_EVERY)

    async def stop(self) -> None:
        if self.scheduler.running:
            # AsyncIOScheduler defers shutdown to the next loop iteration.
            self.scheduler.shutdown(wait=False)
            await asyncio.sleep(0)
        await self.client.aclose()

    async def snapshot_job(self) -> None:
        async with session_scope() as session:
            outcome = await collect(self.client, session, self.user_id, now())
        self.plan_day_jobs(DayConfig.from_user_raw(outcome.user.raw))

    async def close_day_job(self) -> None:
        async with session_scope() as session:
            await close_day(self.client, session, self.user_id, now())
        await self.replan_from_latest_snapshot()

    async def run_cron_job(self) -> None:
        async with session_scope() as session:
            await run_cron(self.client, session, self.user_id, now())
        await self.replan_from_latest_snapshot()

    async def replan_from_latest_snapshot(self) -> None:
        async with session_scope() as session:
            snapshot = await latest_user_snapshot(session, self.user_id)
        if snapshot is not None:
            self.plan_day_jobs(DayConfig.from_user_raw(snapshot.raw))

    def plan_day_jobs(self, config: DayConfig, at: datetime | None = None) -> None:
        at = at or now()
        close_at, cron_at = day_job_times(config, at)
        self.scheduler.add_job(
            self.close_day_job, "date", run_date=close_at, id=CLOSE_DAY_JOB, replace_existing=True
        )
        self.scheduler.add_job(
            self.run_cron_job, "date", run_date=cron_at, id=RUN_CRON_JOB, replace_existing=True
        )
        logger.debug("day jobs planned: close_day %s, run_cron %s", close_at, cron_at)


def day_job_times(config: DayConfig, at: datetime) -> tuple[datetime, datetime]:
    """(close_day, run_cron) instants: the next ones strictly after `at`."""
    next_start = config.next_day_start(at)
    close_at = next_start - CLOSE_DAY_BEFORE
    if close_at <= at:
        close_at += timedelta(days=1)
    cron_at = next_start + RUN_CRON_AFTER
    return close_at, cron_at


def now() -> datetime:
    return datetime.now(UTC)


def build_collector(client: HabiticaClient, user_id: str) -> CollectorJobs:
    return CollectorJobs(client=client, user_id=user_id, scheduler=AsyncIOScheduler(timezone=UTC))
