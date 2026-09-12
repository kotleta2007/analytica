"""aiogram wiring: Telegram updates → the core, `Reply` → Telegram messages.

    TELEGRAM_MODE=polling   long polling in a background task (local dev)
    TELEGRAM_MODE=webhook   Telegram posts to /webhooks/telegram/{secret} (VPS)

Only Telegram ids on `TELEGRAM_ALLOWED_USER_IDS` get through; every one of
them talks as the deployment's single Habitica user. Everyone else is ignored
without a reply.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from aiogram import Bot, Dispatcher, F, Router
from aiogram.dispatcher.event.bases import UNHANDLED
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, Update
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from app.adapters.telegram.render import keyboard, send_reply
from app.config import Settings, get_settings
from app.core import outbound
from app.core.commands import parse_command
from app.core.harness import Harness
from app.core.replies import Button, Reply, User
from app.db.engine import session_scope
from app.db.models import TelegramButton

logger = logging.getLogger(__name__)

EXPIRED_BUTTON = "expired"


class Allowlist(BaseMiddleware):
    """Drops every update whose sender is not on the list, before any handler."""

    def __init__(self, allowed: set[int]) -> None:
        self.allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        sender = data.get("event_from_user")
        if sender is None or sender.id not in self.allowed:
            logger.info("ignoring telegram update from %s", sender.id if sender else "nobody")
            return UNHANDLED
        return await handler(event, data)


class TelegramAdapter:
    def __init__(self, harness: Harness, settings: Settings, bot: Bot | None = None) -> None:
        self.harness = harness
        self.mode = settings.telegram_mode
        self.webhook_url = (
            f"{settings.public_base_url.rstrip('/')}/webhooks/telegram/{settings.webhook_secret}"
        )
        self.allowed = set(settings.telegram_allowed_user_ids)
        self.user = User(settings.habitica_user_id)
        self.bot = bot or Bot(settings.telegram_bot_token)
        self.dispatcher = Dispatcher()
        self.dispatcher.update.outer_middleware(Allowlist(self.allowed))
        self.dispatcher.include_router(self.router())
        self.polling: asyncio.Task[None] | None = None

    def router(self) -> Router:
        router = Router()
        router.message.register(self.on_text, F.text)
        router.message.register(self.on_other)
        router.callback_query.register(self.on_callback)
        return router

    # ---- lifecycle ----

    async def start(self) -> None:
        outbound.register(self.deliver)
        if self.mode == "webhook":
            await self.bot.set_webhook(self.webhook_url)
            logger.info("telegram webhook set to %s", self.webhook_url)
        else:
            await self.bot.delete_webhook()
            self.polling = asyncio.create_task(
                self.dispatcher.start_polling(
                    self.bot, handle_signals=False, close_bot_session=False
                )
            )
            logger.info("telegram polling started")

    async def stop(self) -> None:
        outbound.unregister()
        if self.polling is not None:
            await self.dispatcher.stop_polling()
            await self.polling
            self.polling = None
        await self.bot.session.close()

    async def feed(self, payload: dict[str, Any]) -> None:
        """One update from the webhook route."""
        update = Update.model_validate(payload, context={"bot": self.bot})
        await self.dispatcher.feed_update(self.bot, update)

    # ---- inbound ----

    async def on_text(self, message: Message) -> None:
        text = message.text or ""
        command = parse_command(text)
        if command is not None:
            reply = await self.harness.command_received(self.user, *command)
        else:
            reply = await self.harness.text_message_received(self.user, text)
        await self.send(message.chat.id, reply)

    async def on_other(self, message: Message) -> None:
        # Voice arrives in P04; stickers, photos and the rest get a polite line.
        reply = await self.harness.text_message_received(self.user, "")
        await self.send(message.chat.id, reply)

    async def on_callback(self, callback: CallbackQuery) -> None:
        button = await load_button(callback.data, self.user.id)
        if button is None:
            reply = await self.harness.button_pressed(
                self.user, EXPIRED_BUTTON, {"data": callback.data}
            )
        else:
            reply = await self.harness.button_pressed(self.user, button.action, button.payload)
        await self.bot.answer_callback_query(callback.id)
        await self.send(callback.from_user.id, reply)

    # ---- outbound ----

    async def send(self, chat_id: int, reply: Reply) -> None:
        stored = await store_buttons(reply.buttons, self.user.id)
        await send_reply(self.bot, chat_id, reply, keyboard(stored))

    async def deliver(self, user: User, reply: Reply) -> None:
        """Outbound hook: scheduled messages go to every allowlisted chat."""
        for chat_id in sorted(self.allowed):
            await self.send(chat_id, reply)


async def store_buttons(buttons: list[Button], user_id: str) -> list[tuple[int, Button]]:
    if not buttons:
        return []
    now = datetime.now(UTC)
    async with session_scope() as session:
        rows = [
            TelegramButton(user_id=user_id, action=b.action, payload=b.payload, created_at=now)
            for b in buttons
        ]
        session.add_all(rows)
        await session.flush()
        return [(row.id, button) for row, button in zip(rows, buttons, strict=True)]


async def load_button(data: str | None, user_id: str) -> TelegramButton | None:
    if data is None or not data.isdigit():
        return None
    async with session_scope() as session:
        row = await session.get(TelegramButton, int(data))
    if row is None or row.user_id != user_id:
        return None
    return row


# ---- the webhook route, mounted on the FastAPI app ----

router = APIRouter()


@router.post("/webhooks/telegram/{secret}")
async def receive_telegram_webhook(secret: str, request: Request) -> JSONResponse:
    settings = get_settings()
    if not settings.webhook_secret or secret != settings.webhook_secret:
        logger.warning("telegram webhook with a wrong secret from %s", request.client)
        raise HTTPException(status_code=403, detail="wrong secret")
    adapter: TelegramAdapter | None = getattr(request.app.state, "telegram", None)
    if adapter is None or adapter.mode != "webhook":
        raise HTTPException(status_code=503, detail="telegram webhook mode is off")
    try:
        payload = await request.json()
    except ValueError as error:
        raise HTTPException(status_code=400, detail="body is not JSON") from error
    await adapter.feed(payload)
    return JSONResponse({"ok": True})


def start_telegram_if_configured(harness: Harness | None) -> TelegramAdapter | None:
    settings = get_settings()
    if not (settings.telegram_bot_token and settings.telegram_allowed_user_ids):
        logger.warning("Telegram is not configured; the bot is off")
        return None
    if harness is None:
        logger.warning("Habitica is not configured; the Telegram bot is off")
        return None
    return TelegramAdapter(harness, settings)
