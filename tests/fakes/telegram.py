"""A Telegram bot that records what it was asked to send and never goes online.

`RecordingSession` stands in for aiogram's HTTP session: every API call the
bot makes lands in `requests` as the typed method object (`SendMessage`,
`SendPhoto`, ...), so tests assert on real aiogram types.
"""

import asyncio
from collections.abc import AsyncGenerator
from typing import Any

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import GetFile, GetMe, GetUpdates, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import File, User

TOKEN = "42:TEST-token-for-the-recording-session"


class RecordingSession(BaseSession):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.closed = False
        # What a download returns; None means the test did not expect one.
        self.file_bytes: bytes | None = None

    async def make_request(
        self, bot: Bot, method: TelegramMethod[TelegramType], timeout: int | None = None
    ) -> TelegramType:
        self.requests.append(method)
        if isinstance(method, GetMe):  # start_polling introduces itself first
            return User(id=42, is_bot=True, first_name="Test", username="test_bot")  # type: ignore[return-value]
        if isinstance(method, GetUpdates):
            await asyncio.sleep(0)  # let a polling loop be stopped
            return []  # type: ignore[return-value]
        if isinstance(method, GetFile):
            return File(  # type: ignore[return-value]
                file_id=method.file_id, file_unique_id="u", file_path="voice/file_1.oga"
            )
        return True  # type: ignore[return-value]  # nothing here reads the result

    async def close(self) -> None:
        self.closed = True

    async def stream_content(
        self,
        url: str,
        headers: dict[str, Any] | None = None,
        timeout: int = 30,
        chunk_size: int = 65536,
        raise_for_status: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        if self.file_bytes is None:
            raise AssertionError("this test did not expect a download from Telegram")
        yield self.file_bytes

    def sent(self, method_type: type[TelegramMethod[Any]]) -> list[Any]:
        return [request for request in self.requests if isinstance(request, method_type)]


def recording_bot() -> tuple[Bot, RecordingSession]:
    session = RecordingSession()
    return Bot(TOKEN, session=session), session
