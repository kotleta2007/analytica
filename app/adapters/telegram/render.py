"""`Reply` → Telegram messages: text, inline keyboard, photo, voice note.

Buttons arrive already stored: each one's callback data is the id of its
`telegram_buttons` row, so the 64-byte cap on callback data never matters.
Plain text throughout (no parse mode): task names with `*` or `_` in them
must never break a message.
"""

from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.analytics.charts import to_png
from app.core.replies import Button, Reply

MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024
BUTTONS_PER_ROW = 3


def keyboard(buttons: list[tuple[int, Button]]) -> InlineKeyboardMarkup | None:
    """`(row id, button)` pairs → an inline keyboard, up to three per row."""
    if not buttons:
        return None
    keys = [
        InlineKeyboardButton(text=button.label, callback_data=str(row_id))
        for row_id, button in buttons
    ]
    rows = [keys[i : i + BUTTONS_PER_ROW] for i in range(0, len(keys), BUTTONS_PER_ROW)]
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_reply(
    bot: Bot, chat_id: int, reply: Reply, markup: InlineKeyboardMarkup | None
) -> None:
    """Text and chart first (the keyboard rides on the last of them), then voice."""
    if reply.chart is not None and len(reply.text) <= CAPTION_LIMIT:
        photo = BufferedInputFile(to_png(reply.chart), filename="chart.png")
        await bot.send_photo(chat_id, photo, caption=reply.text, reply_markup=markup)
    else:
        chunks = split_text(reply.text)
        for chunk in chunks[:-1]:
            await bot.send_message(chat_id, chunk)
        await bot.send_message(chat_id, chunks[-1], reply_markup=markup)
        if reply.chart is not None:
            photo = BufferedInputFile(to_png(reply.chart), filename="chart.png")
            await bot.send_photo(chat_id, photo)
    if reply.voice is not None:
        await bot.send_voice(chat_id, BufferedInputFile(reply.voice, filename="reply.ogg"))


def split_text(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Cut at the last newline before the limit, so paragraphs stay whole."""
    chunks: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    chunks.append(rest)
    return chunks
