"""Import the history Habitica still has into daily_records and task_events.

Habitica prunes `task.history` for non-subscribers, so this is partial by
design; it runs at every startup and is idempotent, so it also picks up what
we missed while the bot was down.

What a history entry means (checked against real data, see the fixtures):

- daily, `completed: true`: the user scored it; the entry's timestamp falls
  inside the Habitica day it was completed for.
- daily, `completed: false`, written at a cron time: cron found it due and
  not done, took the damage, and wrote the entry — *after* the day it
  describes ended, so it is about the previous Habitica day. It wins over a
  `completed` entry for that day (a tick synced late lands on the wrong day;
  the damage does not lie). A completed daily gets no cron entry at all.
- daily, `completed: false`, not at a cron time: the user un-ticked it.
  Ignored — a re-tick writes a new entry, a real miss gets a cron entry.
- habit: one entry per scoring, `scoredUp` / `scoredDown` say which way.

Cron times come from `user.history.exp`, which gets one entry per cron.
Days with no entry at all are unknown and stay unrecorded.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.day_close import DayConfig
from app.db.models import DailyRecord, TaskEvent, TaskRow
from app.habitica.client import HabiticaClient
from app.habitica.types import HistoryEntry, parse_epoch_ms, parse_iso, task_from_json

logger = logging.getLogger(__name__)

SOURCE = "backfill"
CRON_MATCH_TOLERANCE = timedelta(seconds=10)


@dataclass
class BackfillOutcome:
    records: list[DailyRecord] = field(default_factory=list)
    events: list[TaskEvent] = field(default_factory=list)


@dataclass(frozen=True)
class DayOutcome:
    was_due: bool
    completed: bool


def cron_times_from_user_raw(raw: dict[str, Any]) -> list[datetime]:
    """Older entries carry epoch milliseconds, newer ones ISO strings."""
    times = []
    for entry in raw.get("history", {}).get("exp", []):
        stamp = entry["date"]
        parsed = parse_iso(stamp) if isinstance(stamp, str) else parse_epoch_ms(stamp)
        if parsed is not None:
            times.append(parsed)
    return sorted(times)


def is_cron_time(at: datetime, cron_times: list[datetime]) -> bool:
    return any(abs(at - cron) <= CRON_MATCH_TOLERANCE for cron in cron_times)


def daily_days_from_history(
    entries: list[HistoryEntry], config: DayConfig, cron_times: list[datetime]
) -> dict[date, DayOutcome]:
    """Fold a daily's history into one outcome per Habitica day."""
    days: dict[date, DayOutcome] = {}
    missed: set[date] = set()
    for entry in sorted(entries, key=lambda e: e.at):
        day = config.habitica_day(entry.at)
        if entry.completed:
            if day not in missed:
                days[day] = DayOutcome(was_due=bool(entry.is_due), completed=True)
        elif is_cron_time(entry.at, cron_times):
            ended_day = day - timedelta(days=1)
            days[ended_day] = DayOutcome(was_due=bool(entry.is_due), completed=False)
            missed.add(ended_day)
        # else: an un-tick; the day's fate is decided by a later entry
    return days


async def backfill(client: HabiticaClient, session: AsyncSession, user_id: str) -> BackfillOutcome:
    """Needs the tasks to be in the DB already: run collect() first."""
    user = await client.get_user()
    config = DayConfig.from_user_raw(user.raw)
    cron_times = cron_times_from_user_raw(user.raw)

    rows = list(
        await session.scalars(
            select(TaskRow).where(TaskRow.user_id == user_id, TaskRow.type.in_(("daily", "habit")))
        )
    )
    outcome = BackfillOutcome()
    for row in rows:
        task = task_from_json(row.raw)
        if not task.history:
            continue
        existing_events = await backfilled_event_times(session, row.id)
        if task.type == "daily":
            outcome.records += await backfill_daily_records(
                session, row, task.history, config, cron_times
            )
            outcome.events += [
                completed_event(row, entry)
                for entry in task.history
                if entry.completed and entry.at not in existing_events
            ]
        else:
            outcome.events += [
                scored_event(row, entry)
                for entry in task.history
                if entry.at not in existing_events
            ]

    session.add_all(outcome.events)
    await session.flush()
    logger.info("backfill: %d daily records, %d events", len(outcome.records), len(outcome.events))
    return outcome


async def backfilled_event_times(session: AsyncSession, task_id: int) -> set[datetime]:
    result = await session.scalars(
        select(TaskEvent.at).where(TaskEvent.task_id == task_id, TaskEvent.source == SOURCE)
    )
    return set(result)


async def backfill_daily_records(
    session: AsyncSession,
    row: TaskRow,
    history: list[HistoryEntry],
    config: DayConfig,
    cron_times: list[datetime],
) -> list[DailyRecord]:
    """Insert what the history says for days we have no record of; never overwrite."""
    existing = set(
        await session.scalars(select(DailyRecord.day).where(DailyRecord.task_id == row.id))
    )
    records = [
        DailyRecord(
            user_id=row.user_id,
            task_id=row.id,
            day=day,
            was_due=outcome.was_due,
            completed=outcome.completed,
            source=SOURCE,
        )
        for day, outcome in daily_days_from_history(history, config, cron_times).items()
        if day not in existing
    ]
    session.add_all(records)
    return records


def completed_event(row: TaskRow, entry: HistoryEntry) -> TaskEvent:
    return TaskEvent(
        user_id=row.user_id,
        task_id=row.id,
        at=entry.at,
        kind="completed",
        source=SOURCE,
        raw={"value": entry.value, "isDue": entry.is_due},
    )


def scored_event(row: TaskRow, entry: HistoryEntry) -> TaskEvent:
    direction = "down" if (entry.scored_down or 0) > (entry.scored_up or 0) else "up"
    return TaskEvent(
        user_id=row.user_id,
        task_id=row.id,
        at=entry.at,
        kind="scored",
        direction=direction,
        source=SOURCE,
        raw={"value": entry.value},
    )
