"""`Reply` → Telegram messages: text, inline keyboard, photo, voice note.

Buttons arrive already stored: each one's callback data is the id of its
`telegram_buttons` row, so the 64-byte cap on callback data never matters.

The model writes Markdown (`**bold**`, `*italic*`, `` `code` ``, lists);
Telegram renders HTML. `to_html` converts the common subset and escapes the
rest, so a task name with `<` or `&` in it is shown, not parsed. Telegram's
own MarkdownV2 is not an option: an unescaped `.` or `-` rejects the whole
message. If Telegram still refuses the entities, the text is resent plain.
"""

import html
import logging
import re

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.analytics.charts import to_png
from app.core.replies import Button, Reply

logger = logging.getLogger(__name__)

MESSAGE_LIMIT = 4096
CAPTION_LIMIT = 1024
BUTTONS_PER_ROW = 3
PARSE_MODE = "HTML"


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
        await send_formatted(
            lambda text, mode: bot.send_photo(
                chat_id, photo, caption=text, parse_mode=mode, reply_markup=markup
            ),
            reply.text,
        )
    else:
        chunks = split_text(reply.text)
        for chunk in chunks[:-1]:
            await send_formatted(
                lambda text, mode: bot.send_message(chat_id, text, parse_mode=mode), chunk
            )
        await send_formatted(
            lambda text, mode: bot.send_message(
                chat_id, text, parse_mode=mode, reply_markup=markup
            ),
            chunks[-1],
        )
        if reply.chart is not None:
            photo = BufferedInputFile(to_png(reply.chart), filename="chart.png")
            await bot.send_photo(chat_id, photo)
    if reply.voice is not None:
        # mp3 from the synthesiser; Telegram shows it as a voice note like Ogg/Opus.
        await bot.send_voice(chat_id, BufferedInputFile(reply.voice, filename="reply.mp3"))


async def send_formatted(send, text: str) -> None:
    """`send(text, parse_mode)`: HTML first; plain text if Telegram rejects the entities."""
    try:
        await send(to_html(text), PARSE_MODE)
    except TelegramBadRequest as error:
        if "parse entities" not in str(error).lower():
            raise
        logger.warning("telegram rejected the formatting, sending plain: %s", error)
        await send(text, None)


# ---- Markdown → Telegram HTML ----

FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
INLINE_CODE = re.compile(r"`([^`\n]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")
STRIKE = re.compile(r"~~(.+?)~~")
LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*$", re.MULTILINE)
BULLET = re.compile(r"^(\s*)[-*]\s+", re.MULTILINE)


def to_html(text: str) -> str:
    """The Markdown the model writes → the HTML subset Telegram renders.

    Code spans are protected first so nothing inside them is touched; every
    other character is HTML-escaped before the inline marks become tags.
    """
    spans: list[str] = []

    def keep(rendered: str) -> str:
        spans.append(rendered)
        return f"\x00{len(spans) - 1}\x00"

    text = FENCE.sub(lambda m: keep(f"<pre>{html.escape(m.group(1).rstrip())}</pre>"), text)
    text = INLINE_CODE.sub(lambda m: keep(f"<code>{html.escape(m.group(1))}</code>"), text)
    text = html.escape(text, quote=False)
    text = LINK.sub(lambda m: keep(f'<a href="{m.group(2)}">{m.group(1)}</a>'), text)
    text = HEADING.sub(r"<b>\1</b>", text)
    text = BOLD.sub(lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    text = ITALIC.sub(lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)
    text = STRIKE.sub(r"<s>\1</s>", text)
    text = BULLET.sub(r"\1• ", text)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], text)


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
