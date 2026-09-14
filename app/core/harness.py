"""The product: four entry points, one per kind of inbound message.

Adapters translate their platform's update into one of these calls and the
returned `Reply` back into platform messages. Nothing here knows about
Telegram or terminals.

Every entry point runs one database transaction: it logs the inbound message,
loads the user's settings, produces the reply and logs that too. A handler
that raises still leaves the inbound message logged, and the user gets a
"something went wrong" line in their language instead of silence.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core import commands, conversation, voice
from app.core.i18n import t
from app.core.replies import Reply, User
from app.core.turn import Turn, load_settings
from app.db.engine import session_scope
from app.db.models import Message
from app.habitica.client import HabiticaClient
from app.llm.client import LlmClient
from app.speech.asr import AsrClient

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class Harness:
    habitica: HabiticaClient
    llm: LlmClient | None = None
    asr: AsrClient | None = None
    clock: Callable[[], datetime] = utcnow
    tz: ZoneInfo = field(default_factory=lambda: ZoneInfo(get_settings().timezone))

    async def text_message_received(self, user: User, text: str) -> Reply:
        """Free text. An empty text is an adapter saying "I got something I can't read"."""

        async def handle(turn: Turn) -> Reply:
            if not text.strip():
                return Reply(t("unsupported_message", turn.language))
            dropped = await voice.drop_pending(turn)
            reply = await conversation.answer(turn, text.strip())
            reply.text = conversation.prefix(dropped, reply.text)
            return reply

        return await self._turn(user, "text", text, handle)

    async def voice_message_received(self, user: User, audio: bytes, mime_type: str) -> Reply:
        async def handle(turn: Turn) -> Reply:
            return await voice.receive(turn, self.asr, audio, mime_type)

        return await self._turn(user, "voice", f"<{mime_type}, {len(audio)} bytes>", handle)

    async def button_pressed(self, user: User, action: str, payload: dict[str, Any]) -> Reply:
        async def handle(turn: Turn) -> Reply:
            if action == conversation.tools.CONFIRM_ACTION:
                return await conversation.confirm(turn, payload)
            if action == conversation.tools.CANCEL_ACTION:
                return await conversation.cancel(turn, payload)
            return Reply(t("unknown_button", turn.language))

        return await self._turn(user, "button", action, handle)

    async def command_received(self, user: User, command: str, args: str) -> Reply:
        async def handle(turn: Turn) -> Reply:
            dropped = await voice.drop_pending(turn) or await conversation.drop_pending(turn)
            reply = await commands.run(turn, command, args)
            reply.text = conversation.prefix(dropped, reply.text)
            return reply

        return await self._turn(user, "command", f"/{command} {args}".strip(), handle)

    async def _turn(
        self, user: User, kind: str, text: str, handle: Callable[[Turn], Awaitable[Reply]]
    ) -> Reply:
        async with session_scope() as session:
            now = self.clock()
            settings = await load_settings(session, user.id)
            inbound = await log_message(session, user.id, now, "in", kind, text)
            turn = Turn(session, user, settings, self.habitica, now, self.tz, self.llm, inbound)
            try:
                reply = await handle(turn)
            except Exception:
                logger.exception("%s turn failed for user %s", kind, user.id)
                reply = Reply(t("something_went_wrong", settings.language))
            # The reply belongs to the session the inbound message was attached to, if any.
            await log_message(
                session,
                user.id,
                self.clock(),
                "out",
                "text",
                reply.text,
                session_id=inbound.session_id,
                llm=None if inbound.session_id is None else assistant_message(reply.text),
            )
        return reply


def assistant_message(text: str) -> dict[str, Any]:
    return {"role": "assistant", "content": text}


async def log_message(
    session: AsyncSession,
    user_id: str,
    at: datetime,
    direction: str,
    kind: str,
    text: str,
    *,
    session_id: int | None = None,
    llm: dict[str, Any] | None = None,
) -> Message:
    row = Message(
        user_id=user_id,
        at=at,
        direction=direction,
        kind=kind,
        text=text,
        session_id=session_id,
        llm=llm,
    )
    session.add(row)
    await session.flush()
    return row
