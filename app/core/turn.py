"""One inbound message and everything a handler needs to answer it."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.replies import User
from app.db.models import UserSettings
from app.habitica.client import HabiticaClient


@dataclass
class Turn:
    session: AsyncSession
    user: User
    settings: UserSettings
    habitica: HabiticaClient
    now: datetime
    tz: ZoneInfo

    @property
    def language(self) -> str:
        return self.settings.language


async def load_settings(session: AsyncSession, user_id: str) -> UserSettings:
    """The user's settings row, created with the defaults on first contact."""
    settings = await session.get(UserSettings, user_id)
    if settings is None:
        settings = UserSettings(user_id=user_id)
        session.add(settings)
        await session.flush()
    return settings
