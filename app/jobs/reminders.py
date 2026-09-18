"""The daily reminder: "still due today: X, Y" at the user's reminder time.

`user_settings.reminder_time` (`HH:MM` in `TIMEZONE`, None = off) is checked
once a minute by the scheduler; `last_reminder_day` makes it fire once per
local day, and a restart after the reminder time sends it late rather than
twice. Nothing to say, nothing sent — the day still counts as reminded.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.collector.snapshots import collect_if_stale
from app.core.i18n import parse_clock, t
from app.core.replies import Reply
from app.core.tasks import KnownTask, bullet_list, load_known_tasks
from app.core.turn import load_settings
from app.db.models import UserSettings
from app.habitica.client import HabiticaClient

SNAPSHOT_MAX_AGE = timedelta(minutes=10)
DEFAULT_REMINDER_TIME = "21:00"


def reminder_due(settings: UserSettings, now: datetime, tz: ZoneInfo) -> bool:
    if settings.reminder_time is None:
        return False
    local = now.astimezone(tz)
    if settings.last_reminder_day == local.date():
        return False
    return local.time() >= parse_clock(settings.reminder_time)


def due_and_incomplete(known: list[KnownTask]) -> list[KnownTask]:
    return [
        k for k in known if k.task.type == "daily" and bool(k.task.is_due) and not k.task.completed
    ]


async def reminder_reply(
    client: HabiticaClient, session: AsyncSession, user_id: str, now: datetime, tz: ZoneInfo
) -> Reply | None:
    """What to send now, or None: not the time yet, already sent today, or nothing due."""
    settings = await load_settings(session, user_id)
    if not reminder_due(settings, now, tz):
        return None
    settings.last_reminder_day = now.astimezone(tz).date()
    await session.flush()
    await collect_if_stale(client, session, user_id, now, SNAPSHOT_MAX_AGE)
    dailies = due_and_incomplete(await load_known_tasks(session, user_id))
    if not dailies:
        return None
    return Reply(t("reminder_dailies", settings.language, dailies=bullet_list(dailies)))
