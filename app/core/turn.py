"""One inbound message and everything a handler needs to answer it."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.replies import User
from app.core.sessions import Summarizer
from app.db.models import Message, UserSettings
from app.habitica.client import HabiticaClient
from app.llm.client import LlmClient, chat
from app.llm.models import CHEAP, model_id


@dataclass
class Turn:
    session: AsyncSession
    user: User
    settings: UserSettings
    habitica: HabiticaClient
    now: datetime
    tz: ZoneInfo
    llm: LlmClient | None = None
    # The row the harness logged for the inbound message, so a handler can attach it to a session.
    inbound: Message | None = None
    # Whether a speech synthesiser is configured: `/tts on` is refused without one.
    can_speak: bool = False

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


def summarizer(turn: Turn) -> Summarizer | None:
    """Session summaries use the cheap model; None when no model is configured."""
    llm = turn.llm
    if llm is None:
        return None

    async def summarize(messages: list[dict]) -> str:
        result = await chat(
            llm, turn.session, turn.user.id, turn.now, messages, None, model_id(CHEAP)
        )
        return result.text.strip()

    return summarize
