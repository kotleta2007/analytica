"""Record yesterday's dailies before the bot turns the day.

Habitica's own client, opened after day start, asks "check off any dailies
you did yesterday" before it runs cron; the bot's scheduled `POST /cron`
skipped that step and zeroed streaks that were earned but not ticked. So
half an hour before day start the bot asks the same question — one button
per unticked due daily and *Nothing else* — and its cron waits for the answer
(`user_settings.cron_hold_until`, at most `CRON_GRACE_MIN` after day start).

Nothing but `POST /cron` turns the day, so a tick from these buttons after
day start still counts for the day that is ending, exactly as in the app.
The job that asks lives in `jobs/scheduler.py`; this module builds the
question and handles the two buttons.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.collector import day_close
from app.collector.day_close import (
    RUN_CRON_AFTER,
    CronOutcome,
    DayConfig,
    ending_day,
    set_cron_hold,
)
from app.collector.snapshots import CollectOutcome, collect
from app.core import conversation, gear
from app.core.commands import fmt
from app.core.i18n import format_clock, t
from app.core.replies import Button, Reply
from app.core.tasks import (
    KnownTask,
    TaskNotFound,
    bullet_list,
    due_dailies,
    load_known_tasks,
    resolve_task,
    unfinished_dailies,
)
from app.core.turn import Turn
from app.db.models import UserSettings
from app.habitica.client import HabiticaClient, HabiticaError
from app.habitica.types import parse_iso, user_stats_from_json

logger = logging.getLogger(__name__)

TICK_ACTION = "tick_daily"
DONE_ACTION = "day_turn_done"


@dataclass(frozen=True)
class Window:
    day: date  # the Habitica day that is ending
    cron_at: datetime  # when the bot turns the day if nothing holds it (UTC)
    hold_until: datetime  # how long it waits for the answer at most (UTC)


def window(user_raw: dict[str, Any], now: datetime, grace: timedelta) -> Window:
    """Asked on time, the wait ends `grace` after day start; asked late (a restart
    after day start), the user still gets `grace` from the question."""
    day = ending_day(user_raw, now)
    start = DayConfig.from_user_raw(user_raw).day_start_utc(day + timedelta(days=1))
    return Window(
        day=day, cron_at=start + RUN_CRON_AFTER, hold_until=max(start + grace, now + grace)
    )


def unticked_dailies(known: list[KnownTask]) -> list[KnownTask]:
    """Due, not completed, and not opted out of the question in the app (`yesterDaily`)."""
    return [
        k
        for k in known
        if k.task.type == "daily"
        and bool(k.task.is_due)
        and not k.task.completed
        and k.row.raw.get("yesterDaily", True)
    ]


def day_has_turned(user_raw: dict[str, Any], day: date) -> bool:
    """Has cron run for `day` — by us at the deadline, or by the app meanwhile?"""
    last_cron = parse_iso(user_raw.get("lastCron"))
    if last_cron is None:
        return False
    return last_cron >= DayConfig.from_user_raw(user_raw).day_start_utc(day + timedelta(days=1))


# ---- the question ----


def ask(dailies: list[KnownTask], window_: Window, language: str, tz: ZoneInfo) -> Reply:
    text = t(
        "day_turn_ask",
        language,
        dailies=bullet_list(dailies),
        cron_time=format_clock(window_.cron_at.astimezone(tz)),
        deadline=format_clock(window_.hold_until.astimezone(tz)),
    )
    return Reply(text, buttons=buttons_for(dailies, window_.day, language))


def buttons_for(dailies: list[KnownTask], day: date, language: str) -> list[Button]:
    payload = {"day": day.isoformat()}
    return [
        Button(f"✅ {k.task.text}", TICK_ACTION, {**payload, "task_id": k.row.habitica_id})
        for k in dailies
    ] + [Button(t("day_turn_nothing_else", language), DONE_ACTION, payload)]


# ---- the buttons ----


async def tick(turn: Turn, payload: dict[str, Any]) -> Reply:
    day = date.fromisoformat(payload["day"])
    outcome = await collect(turn.habitica, turn.session, turn.user.id, turn.now)
    if day_has_turned(outcome.user.raw, day):
        return too_late(turn, outcome)
    try:
        known = await resolve_task(turn.session, turn.user.id, payload["task_id"])
    except TaskNotFound:
        return Reply(t("unknown_button", turn.language))
    await conversation.record_press(turn, f"{TICK_ACTION}: {known.task.text}")
    try:
        await turn.habitica.score_task(known.row.habitica_id, "up")
    except (HabiticaError, httpx.HTTPError) as error:
        logger.warning("could not tick %s from the day-turn window: %s", known.task.text, error)
        return Reply(t("habitica_error", turn.language, error=str(error)))
    ticked = t("day_turn_ticked", turn.language, task=known.task.text)

    after = await collect(
        turn.habitica, turn.session, turn.user.id, turn.now, source=conversation.BOT_SOURCE
    )
    remaining = unticked_dailies(await load_known_tasks(turn.session, turn.user.id))
    if remaining:
        rest = t("day_turn_remaining", turn.language, dailies=bullet_list(remaining))
        text = ticked + "\n" + rest
        return Reply(text, buttons=buttons_for(remaining, day, turn.language))
    return Reply(ticked + "\n" + await settle(turn, after))


async def done(turn: Turn, payload: dict[str, Any]) -> Reply:
    day = date.fromisoformat(payload["day"])
    outcome = await collect(turn.habitica, turn.session, turn.user.id, turn.now)
    if day_has_turned(outcome.user.raw, day):
        return too_late(turn, outcome)
    await conversation.record_press(turn, DONE_ACTION)
    return Reply(await settle(turn, outcome))


async def settle(turn: Turn, outcome: CollectOutcome) -> str:
    """The question is answered: turn the day now if it is time, else let the schedule do it."""
    if outcome.user.raw.get("needsCron"):
        turned = await turn_day(turn.habitica, turn.session, turn.user.id, turn.now, turn.settings)
        return new_day_message(turned, turn.language)
    # Answered: a hold in the past lets the scheduled cron proceed (None would mean "not asked").
    await set_cron_hold(turn.session, turn.user.id, turn.now)
    cron_at = window(outcome.user.raw, turn.now, timedelta(0)).cron_at
    return t("day_turn_ok", turn.language, cron_time=format_clock(cron_at.astimezone(turn.tz)))


def too_late(turn: Turn, outcome: CollectOutcome) -> Reply:
    last_cron = parse_iso(outcome.user.raw.get("lastCron")) or turn.now
    return Reply(
        t("day_turn_too_late", turn.language, time=format_clock(last_cron.astimezone(turn.tz)))
    )


# ---- the turn itself: gear on, cron, gear for the day, the new-day message ----


@dataclass
class DayTurned:
    cron: CronOutcome
    worn_for_cron: str | None  # "con" / "int", or None when no set is configured
    worn_now: str | None  # "per", or None
    due_today: list[KnownTask]


async def turn_day(
    client: HabiticaClient,
    session: AsyncSession,
    user_id: str,
    now: datetime,
    settings: UserSettings,
) -> DayTurned:
    """What both the scheduler and the buttons run: CON or INT for the cron, `POST /cron`,
    PER for the day. The sets are the user's (`/gear`); an empty set is skipped."""
    worn: dict[str, str | None] = {}

    async def dress_for_cron(outcome: CollectOutcome) -> None:
        stats = user_stats_from_json(outcome.user.raw)
        unfinished = unfinished_dailies(await load_known_tasks(session, user_id))
        worn["cron"] = await wear_or_skip(
            client, stats.equipped, settings, gear.before_cron(bool(unfinished))
        )

    cron = await day_close.turn_day(client, session, user_id, now, dress_for_cron)
    after = user_stats_from_json(cron.after.user.raw)
    worn_now = await wear_or_skip(client, after.equipped, settings, "per")
    due_today = due_dailies(await load_known_tasks(session, user_id))
    logger.info("day turned: wore %s for cron, %s now", worn.get("cron"), worn_now)
    return DayTurned(cron, worn.get("cron"), worn_now, due_today)


async def wear_or_skip(
    client: HabiticaClient, equipped: dict[str, str], settings: UserSettings, stat: str
) -> str | None:
    """The gear is a bonus; the cron is the point. A refused equip must not keep the day
    from turning (or the new-day message from going out), so it is logged and skipped."""
    try:
        return await gear.wear(client, equipped, settings, stat)
    except (HabiticaError, httpx.HTTPError) as error:
        logger.warning("could not wear the %s set for the day turn: %s", stat, error)
        return None


def new_day_message(turned: DayTurned, language: str) -> str:
    """HP lost overnight, mana now, the dailies due today — one message, numbers from Python."""
    after = user_stats_from_json(turned.cron.after.user.raw)
    text = t(
        "new_day",
        language,
        hp_lost=fmt(round(turned.cron.hp_lost, 1)),
        hp=fmt(after.hp),
        max_hp=after.max_hp,
        mp=fmt(after.mp),
        max_mp=after.max_mp,
        dailies=bullet_list(turned.due_today) or t("new_day_nothing_due", language),
    )
    if turned.worn_for_cron or turned.worn_now:
        text += "\n" + t(
            "new_day_gear",
            language,
            for_cron=(turned.worn_for_cron or "—").upper(),
            now=(turned.worn_now or "—").upper(),
        )
    return text
