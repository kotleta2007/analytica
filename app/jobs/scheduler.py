"""APScheduler wiring for the collector and the day turn.

    every 30 min        snapshot_job:       collect(), then re-plan the day jobs
    dayStart − 30 min   ask_unticked_job:   "still unticked: X, Y?" with buttons; holds our cron
    dayStart − 2 min    close_day_job:      close_day()
    dayStart + 1 min    run_cron_job:       turn_day() — or wait, while the question is open

The day jobs are one-shot `date` triggers, re-planned after every collection
round from the `dayStart` and `timezoneOffset` in the freshest `/user`
response — so a DST change or a changed day start is picked up within half
an hour, without configuration.

Only `POST /cron` turns the Habitica day, so the collector keeps its schedule
through the window; `user_settings.cron_hold_until` says where the question
stands (None = not asked, future = waiting, past = answered) and only our own
cron looks at it. A restart after day start plans the cron job at once:
`run_cron_job` then asks first if nobody did.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.backfill import backfill
from app.collector.day_close import (
    ASK_BEFORE,
    CLOSE_DAY_BEFORE,
    RUN_CRON_AFTER,
    DayConfig,
    close_day,
    cron_hold,
    set_cron_hold,
    turn_day,
)
from app.collector.snapshots import CollectOutcome, collect, latest_user_snapshot
from app.config import get_settings
from app.core import day_turn, outbound
from app.core.replies import Reply, User
from app.core.tasks import load_known_tasks
from app.core.turn import load_settings
from app.db.engine import session_scope
from app.habitica.client import HabiticaClient

logger = logging.getLogger(__name__)

SNAPSHOT_EVERY = timedelta(minutes=30)
# A date job that could not run on time (busy loop, brief outage) still runs within this.
DAY_JOB_GRACE = timedelta(minutes=10)

SNAPSHOT_JOB = "snapshot"
ASK_JOB = "ask_unticked"
CLOSE_DAY_JOB = "close_day"
RUN_CRON_JOB = "run_cron"


@dataclass
class Question:
    reply: Reply
    hold_until: datetime
    count: int


@dataclass
class DayJobTimes:
    ask: datetime
    close: datetime
    cron: datetime


@dataclass
class CollectorJobs:
    client: HabiticaClient
    user_id: str
    scheduler: AsyncIOScheduler
    grace: timedelta = field(
        default_factory=lambda: timedelta(minutes=get_settings().cron_grace_min)
    )
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo(get_settings().timezone))

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
        at = now()
        async with session_scope() as session:
            outcome = await collect(self.client, session, self.user_id, at)
            hold = await cron_hold(session, self.user_id)
        self.plan_day_jobs(outcome.user.raw, hold, at)

    async def ask_unticked_job(self) -> None:
        at = now()
        async with session_scope() as session:
            outcome = await collect(self.client, session, self.user_id, at)
            question = await self.question(session, outcome, at)
        hold = await self.put(question, at)
        self.plan_day_jobs(outcome.user.raw, hold, at)

    async def close_day_job(self) -> None:
        async with session_scope() as session:
            await close_day(self.client, session, self.user_id, now())
        await self.replan_from_latest_snapshot()

    async def run_cron_job(self) -> None:
        at = now()
        async with session_scope() as session:
            hold = await cron_hold(session, self.user_id)
            if hold is None:
                outcome = await collect(self.client, session, self.user_id, at)
                if not outcome.user.raw.get("needsCron"):
                    # Already turned — by a button after day start (`settle` clears the hold) or
                    # in the app — and this is the job armed for that day's hold. Asking now
                    # would put the new day's dailies up as "still unticked".
                    logger.info("the day already turned; cron not needed until the next one")
                    self.plan_day_jobs(outcome.user.raw, None, at)
                    return
                # Nobody asked (the process was down at dayStart − 30): ask now, then wait.
                question = await self.question(session, outcome, at)
        if hold is None:
            hold = await self.put(question, at)
        if hold > at:
            logger.info("cron held until %s: the unticked-dailies question is open", hold)
            self.arm(RUN_CRON_JOB, self.run_cron_job, hold)
            return
        async with session_scope() as session:
            await turn_day(self.client, session, self.user_id, at)
        await self.replan_from_latest_snapshot()

    async def question(
        self, session: AsyncSession, outcome: CollectOutcome, at: datetime
    ) -> "Question | None":
        """The question to put before our cron, or None when every due daily is ticked."""
        dailies = day_turn.unticked_dailies(await load_known_tasks(session, self.user_id))
        if not dailies:
            return None
        settings = await load_settings(session, self.user_id)
        window = day_turn.window(outcome.user.raw, at, self.grace)
        reply = day_turn.ask(dailies, window, settings.language, self.tz)
        return Question(reply, window.hold_until, len(dailies))

    async def put(self, question: "Question | None", at: datetime) -> datetime:
        """Send the question and record the hold it leaves; returns that hold.

        Nothing to ask, or no adapter to deliver to, leaves a hold in the
        past: answered, as far as the schedule is concerned. Sent outside the
        collection's session: delivery logs the message in a session of its own.
        """
        hold = at
        if question is None:
            logger.info("nothing unticked before day start; cron runs on schedule")
        elif await outbound.send_to_user(User(self.user_id), question.reply):
            hold = question.hold_until
            logger.info(
                "asked about %d unticked dailies; cron waits until %s", question.count, hold
            )
        async with session_scope() as session:
            await set_cron_hold(session, self.user_id, hold)
        return hold

    async def replan_from_latest_snapshot(self) -> None:
        async with session_scope() as session:
            snapshot = await latest_user_snapshot(session, self.user_id)
            hold = await cron_hold(session, self.user_id)
        if snapshot is not None:
            self.plan_day_jobs(snapshot.raw, hold)

    def plan_day_jobs(
        self, user_raw: dict[str, Any], hold: datetime | None, at: datetime | None = None
    ) -> None:
        at = at or now()
        times = day_job_times(
            DayConfig.from_user_raw(user_raw), at, hold, bool(user_raw.get("needsCron"))
        )
        self.arm(ASK_JOB, self.ask_unticked_job, times.ask)
        self.arm(CLOSE_DAY_JOB, self.close_day_job, times.close)
        self.arm(RUN_CRON_JOB, self.run_cron_job, times.cron)
        logger.debug("day jobs planned: %s", times)

    def arm(self, job_id: str, job: Callable[[], Awaitable[None]], run_date: datetime) -> None:
        self.scheduler.add_job(
            job,
            "date",
            run_date=run_date,
            id=job_id,
            replace_existing=True,
            misfire_grace_time=int(DAY_JOB_GRACE.total_seconds()),
        )


def day_job_times(
    config: DayConfig, at: datetime, hold: datetime | None = None, needs_cron: bool = False
) -> DayJobTimes:
    """The next ask / close / cron instants strictly after `at`.

    While the question is open (`hold` in the future) our cron runs at the
    hold, not tomorrow. An account already waiting for cron with nothing
    holding it — a restart after day start — gets the cron job right away;
    `run_cron_job` asks first if the question was never put.
    """
    next_start = config.next_day_start(at)
    ask_at = next_start - ASK_BEFORE
    if ask_at <= at:
        ask_at += timedelta(days=1)
    close_at = next_start - CLOSE_DAY_BEFORE
    if close_at <= at:
        close_at += timedelta(days=1)
    cron_at = next_start + RUN_CRON_AFTER
    if hold is not None and at < hold:
        cron_at = min(cron_at, hold)
    elif needs_cron:
        cron_at = at + timedelta(seconds=5)
    return DayJobTimes(ask=ask_at, close=close_at, cron=cron_at)


def now() -> datetime:
    return datetime.now(UTC)


def build_collector(client: HabiticaClient, user_id: str) -> CollectorJobs:
    return CollectorJobs(client=client, user_id=user_id, scheduler=AsyncIOScheduler(timezone=UTC))
