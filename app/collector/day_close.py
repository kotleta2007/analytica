"""The Habitica day: where it starts, closing it, and running cron on purpose.

Habitica's day starts at `preferences.dayStart` o'clock in the user's local
time. `preferences.timezoneOffset` is the JavaScript offset, minutes *behind*
UTC (Zurich in summer: -120), and the app updates it when the user logs in
after a DST change — so the boundary in UTC moves with the season, and we
always take both numbers from the latest `/user` response.

Nothing turns the day but `POST /cron` (Habitica's server runs cron nowhere
else; verified in its source). After day start, `/user` reports `needsCron`
and every read and score keeps working against the day that is ending — the
window in which the app shows "Record Yesterday's Dailies". So:

    dayStart - 2 min   close_day(): final snapshot, daily_records for the ending day
    dayStart + 1 min   turn_day():  records again (they may have changed in the
                       window), POST /cron, snapshot — unless the question about
                       unticked dailies is still open (`user_settings.cron_hold_until`)
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.snapshots import (
    CollectOutcome,
    collect,
    latest_user_snapshot,
    load_latest_snapshots,
)
from app.db.models import DailyRecord, TaskRow, UserSettings, UserSnapshot
from app.habitica.client import HabiticaClient

logger = logging.getLogger(__name__)

CLOSE_DAY_SOURCE = "close_day"


@dataclass(frozen=True)
class DayConfig:
    day_start: int
    timezone_offset: int

    @classmethod
    def from_user_raw(cls, raw: dict[str, Any]) -> "DayConfig":
        preferences = raw["preferences"]
        return cls(int(preferences["dayStart"]), int(preferences["timezoneOffset"]))

    def local_time(self, at: datetime) -> datetime:
        return at.astimezone(UTC) - timedelta(minutes=self.timezone_offset)

    def habitica_day(self, at: datetime) -> date:
        """The Habitica day an instant belongs to."""
        return (self.local_time(at) - timedelta(hours=self.day_start)).date()

    def day_start_utc(self, day: date) -> datetime:
        """The UTC instant a Habitica day begins."""
        local = datetime(day.year, day.month, day.day, self.day_start, tzinfo=UTC)
        return local + timedelta(minutes=self.timezone_offset)

    def next_day_start(self, after: datetime) -> datetime:
        return self.day_start_utc(self.habitica_day(after) + timedelta(days=1))


def ending_day(user_raw: dict[str, Any], now: datetime) -> date:
    """The Habitica day the next cron will close.

    Before day start that is the current day; after it, while `needsCron` is
    set, the calendar has moved on but the account has not.
    """
    day = DayConfig.from_user_raw(user_raw).habitica_day(now)
    if user_raw.get("needsCron"):
        day -= timedelta(days=1)
    return day


async def close_day(
    client: HabiticaClient, session: AsyncSession, user_id: str, now: datetime
) -> list[DailyRecord]:
    """Final snapshot of the ending day, then one daily_records row per daily.

    Idempotent: a second run for the same day updates the rows it wrote.
    """
    outcome = await collect(client, session, user_id, now)
    return await close_from(session, user_id, outcome, now)


async def close_from(
    session: AsyncSession, user_id: str, outcome: CollectOutcome, now: datetime
) -> list[DailyRecord]:
    day = ending_day(outcome.user.raw, now)
    records = await write_daily_records(session, user_id, day)
    logger.info("closed habitica day %s: %d daily records", day, len(records))
    return records


async def write_daily_records(session: AsyncSession, user_id: str, day: date) -> list[DailyRecord]:
    dailies = list(
        await session.scalars(
            select(TaskRow).where(
                TaskRow.user_id == user_id,
                TaskRow.type == "daily",
                TaskRow.deleted_at.is_(None),
            )
        )
    )
    latest = await load_latest_snapshots(session, user_id)
    existing = {
        record.task_id: record
        for record in await session.scalars(
            select(DailyRecord).where(DailyRecord.user_id == user_id, DailyRecord.day == day)
        )
    }

    records: list[DailyRecord] = []
    for daily in dailies:
        snapshot = latest.get(daily.id)
        if snapshot is None:
            continue
        record = existing.get(daily.id)
        if record is None:
            record = DailyRecord(
                user_id=user_id, task_id=daily.id, day=day, source=CLOSE_DAY_SOURCE
            )
            session.add(record)
        record.was_due = bool(snapshot.is_due)
        record.completed = bool(snapshot.completed)
        record.source = CLOSE_DAY_SOURCE
        records.append(record)
    await session.flush()
    return records


@dataclass
class CronOutcome:
    before: UserSnapshot | None
    after: CollectOutcome

    @property
    def hp_lost(self) -> float:
        if self.before is None:
            return 0.0
        return max(self.before.hp - self.after.user.hp, 0.0)


async def run_cron(
    client: HabiticaClient, session: AsyncSession, user_id: str, now: datetime
) -> CronOutcome:
    """Trigger the day reset ourselves, then record the state right after it."""
    before = await latest_user_snapshot(session, user_id)
    await client.run_cron()
    after = await collect(client, session, user_id, now)
    logger.info(
        "ran cron: hp %s -> %s, mp %s -> %s",
        None if before is None else before.hp,
        after.user.hp,
        None if before is None else before.mp,
        after.user.mp,
    )
    return CronOutcome(before=before, after=after)


async def turn_day(
    client: HabiticaClient, session: AsyncSession, user_id: str, now: datetime
) -> CronOutcome:
    """Close the ending day as it stands now, run cron, and release the hold.

    The records written at `dayStart - 2` may be stale by now: the user can
    tick dailies until cron, from the bot's buttons or the app. Harmless when
    the app already turned the day — Habitica's cron is a no-op then.
    """
    outcome = await collect(client, session, user_id, now)
    if outcome.user.raw.get("needsCron"):
        await close_from(session, user_id, outcome, now)
    result = await run_cron(client, session, user_id, now)
    await set_cron_hold(session, user_id, None)
    return result


async def cron_hold(session: AsyncSession, user_id: str) -> datetime | None:
    settings = await session.get(UserSettings, user_id)
    return None if settings is None else settings.cron_hold_until


async def set_cron_hold(session: AsyncSession, user_id: str, until: datetime | None) -> None:
    settings = await session.get(UserSettings, user_id)
    if settings is None:
        settings = UserSettings(user_id=user_id)
        session.add(settings)
    settings.cron_hold_until = until
    await session.flush()
