import asyncio
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import (
    AnswerCallbackQuery,
    DeleteWebhook,
    GetFile,
    GetUpdates,
    SendMessage,
    SendPhoto,
    SendVoice,
    SetWebhook,
)
from aiogram.types import InlineKeyboardMarkup

from app.adapters.telegram.bot import (
    EXPIRED_BUTTON,
    TelegramAdapter,
    start_telegram_if_configured,
    store_buttons,
)
from app.adapters.telegram.render import keyboard, send_reply, split_text, to_html
from app.config import get_settings
from app.core.replies import Button, ChartData, Reply, User
from tests.fakes.telegram import RecordingSession, recording_bot

HABITICA_USER = "00000000-0000-4000-8000-000000000001"
ALLOWED = 4242
STRANGER = 9999
SECRET = "s3cret"


class FakeHarness:
    """Records which entry point the adapter chose; answers with a fixed reply."""

    def __init__(self, reply: Reply | None = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.reply = reply or Reply("ok")

    async def text_message_received(self, user: User, text: str) -> Reply:
        self.calls.append(("text", text))
        return self.reply

    async def voice_message_received(self, user: User, audio: bytes, mime_type: str) -> Reply:
        self.calls.append(("voice", (audio, mime_type)))
        return self.reply

    async def button_pressed(self, user: User, action: str, payload: dict[str, Any]) -> Reply:
        self.calls.append(("button", (action, payload)))
        return self.reply

    async def command_received(self, user: User, command: str, args: str) -> Reply:
        self.calls.append(("command", (command, args)))
        return self.reply


def configure(monkeypatch: pytest.MonkeyPatch, mode: str = "polling") -> None:
    monkeypatch.setenv("HABITICA_USER_ID", HABITICA_USER)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "42:TEST")
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", str(ALLOWED))
    monkeypatch.setenv("TELEGRAM_MODE", mode)
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://bot.example.com/")
    monkeypatch.setenv("WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()


@pytest.fixture
def harness() -> FakeHarness:
    return FakeHarness()


@pytest.fixture
async def adapter(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, harness: FakeHarness
) -> AsyncIterator[TelegramAdapter]:
    configure(monkeypatch)
    bot, _session = recording_bot()
    yield TelegramAdapter(harness, get_settings(), bot=bot)
    get_settings.cache_clear()


def session_of(adapter: TelegramAdapter) -> RecordingSession:
    assert isinstance(adapter.bot.session, RecordingSession)
    return adapter.bot.session


def message_update(text: str, sender: int = ALLOWED, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "date": 1789000000,
            "chat": {"id": sender, "type": "private"},
            "from": {"id": sender, "is_bot": False, "first_name": "Operator"},
            "text": text,
        },
    }


def callback_update(data: str, sender: int = ALLOWED) -> dict[str, Any]:
    return {
        "update_id": 7,
        "callback_query": {
            "id": "cb-1",
            "from": {"id": sender, "is_bot": False, "first_name": "Operator"},
            "chat_instance": "ci",
            "data": data,
        },
    }


# ---- render ----


def test_keyboard_puts_three_buttons_per_row_with_row_ids_as_callback_data() -> None:
    buttons = [(n, Button(f"b{n}", "act")) for n in range(1, 5)]

    markup = keyboard(buttons)

    assert isinstance(markup, InlineKeyboardMarkup)
    assert [[b.callback_data for b in row] for row in markup.inline_keyboard] == [
        ["1", "2", "3"],
        ["4"],
    ]
    assert markup.inline_keyboard[0][0].text == "b1"
    assert keyboard([]) is None


async def test_reply_with_buttons_becomes_a_message_with_a_keyboard() -> None:
    bot, session = recording_bot()
    markup = keyboard([(1, Button("✅ Do it", "confirm"))])

    await send_reply(bot, 42, Reply("Delete Workout?"), markup)

    (sent,) = session.sent(SendMessage)
    assert sent.chat_id == 42
    assert sent.text == "Delete Workout?"
    assert sent.reply_markup is markup
    assert sent.parse_mode == "HTML"


async def test_markdown_from_the_model_is_sent_as_telegram_html() -> None:
    bot, session = recording_bot()

    await send_reply(bot, 42, Reply("Done — **Vacuum the floors** is ticked off."), None)

    (sent,) = session.sent(SendMessage)
    assert sent.text == "Done — <b>Vacuum the floors</b> is ticked off."


async def test_rejected_entities_fall_back_to_plain_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    bot, session = recording_bot()
    original = session.make_request

    async def refuse_html(bot_: Any, method: Any, timeout: int | None = None) -> Any:
        if isinstance(method, SendMessage) and method.parse_mode == "HTML":
            session.requests.append(method)
            raise TelegramBadRequest(
                method=method, message="Bad Request: can't parse entities: unclosed tag"
            )
        return await original(bot_, method, timeout)

    session.make_request = refuse_html  # type: ignore[method-assign]
    await send_reply(bot, 42, Reply("**odd"), None)

    html, plain = session.sent(SendMessage)
    assert (html.parse_mode, html.text) == ("HTML", "**odd")
    assert (plain.parse_mode, plain.text) == (None, "**odd")
    assert "rejected the formatting" in caplog.text


@pytest.mark.parametrize(
    ("markdown", "html"),
    [
        ("**8 todos** are overdue", "<b>8 todos</b> are overdue"),
        ("__strong__ and _soft_ and *soft*", "<b>strong</b> and <i>soft</i> and <i>soft</i>"),
        ("- Write email\n- Free disk space", "• Write email\n• Free disk space"),
        ("## Overdue", "<b>Overdue</b>"),
        ("call `score_task`", "call <code>score_task</code>"),
        ('```json\n{"a": 1}\n```', "<pre>{&quot;a&quot;: 1}</pre>"),
        ("[docs](https://habitica.com/x)", '<a href="https://habitica.com/x">docs</a>'),
        ("~~gone~~", "<s>gone</s>"),
        # Task names are shown, not parsed.
        ("HP < 10 & MP > 5", "HP &lt; 10 &amp; MP &gt; 5"),
        ("snake_case_name, 2 * 3 * 4", "snake_case_name, 2 * 3 * 4"),
        ("<b>not a tag</b>", "&lt;b&gt;not a tag&lt;/b&gt;"),
        ("**bold with `code` inside**", "<b>bold with <code>code</code> inside</b>"),
    ],
)
def test_to_html_converts_the_markdown_the_model_writes(markdown: str, html: str) -> None:
    assert to_html(markdown) == html


async def test_chart_becomes_a_photo_with_the_text_as_caption() -> None:
    bot, session = recording_bot()
    chart = ChartData("gold", [date(2026, 9, 1), date(2026, 9, 2)], {"gp": [1.0, 2.0]})

    await send_reply(bot, 42, Reply("Your gold", chart=chart), None)

    assert session.sent(SendMessage) == []
    (photo,) = session.sent(SendPhoto)
    assert photo.caption == "Your gold"
    assert photo.photo.data.startswith(b"\x89PNG")


async def test_voice_is_sent_as_a_voice_note_after_the_text() -> None:
    bot, session = recording_bot()

    await send_reply(bot, 42, Reply("Hi", voice=b"ID3-fake-mp3"), None)

    assert [type(r) for r in session.requests] == [SendMessage, SendVoice]
    (voice,) = session.sent(SendVoice)
    assert voice.voice.data == b"ID3-fake-mp3"
    assert voice.voice.filename == "reply.mp3"


async def test_long_text_is_split_and_the_keyboard_rides_on_the_last_chunk() -> None:
    bot, session = recording_bot()
    text = "\n".join(f"line {n}" for n in range(1000))
    markup = keyboard([(1, Button("ok", "ok"))])

    await send_reply(bot, 42, Reply(text, chart=None), markup)

    sent = session.sent(SendMessage)
    assert len(sent) == 3  # ~8.9k characters
    assert all(len(s.text) <= 4096 for s in sent)
    assert [s.reply_markup for s in sent] == [None, None, markup]
    assert "\n".join(s.text for s in sent) == text  # plain lines: HTML is the same text


def test_split_text_prefers_a_newline_and_never_returns_empty() -> None:
    assert split_text("abc", limit=10) == ["abc"]
    assert split_text("aaaa\nbbbb\ncccc", limit=10) == ["aaaa\nbbbb", "cccc"]
    assert split_text("a" * 25, limit=10) == ["a" * 10, "a" * 10, "a" * 5]


# ---- allowlist and routing ----


async def test_stranger_is_ignored_without_a_reply(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    await adapter.feed(message_update("/stats", sender=STRANGER))

    assert harness.calls == []
    assert session_of(adapter).requests == []


async def test_command_and_text_are_routed_to_the_right_entry_points(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    await adapter.feed(message_update("/language fr", update_id=1))
    await adapter.feed(message_update("done with workout", update_id=2))

    assert harness.calls == [("command", ("language", "fr")), ("text", "done with workout")]
    sent = session_of(adapter).sent(SendMessage)
    assert [(s.chat_id, s.text) for s in sent] == [(ALLOWED, "ok"), (ALLOWED, "ok")]


async def test_reply_buttons_are_stored_and_the_callback_carries_the_row_id(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    harness.reply = Reply("Sure?", buttons=[Button("✅ Do it", "confirm", {"task": "x" * 60})])
    await adapter.feed(message_update("/x"))
    (sent,) = session_of(adapter).sent(SendMessage)
    assert sent.reply_markup is not None
    row_id = sent.reply_markup.inline_keyboard[0][0].callback_data
    assert row_id is not None and row_id.isdigit()

    harness.reply = Reply("Done")
    await adapter.feed(callback_update(row_id))

    assert harness.calls[-1] == ("button", ("confirm", {"task": "x" * 60}))
    assert session_of(adapter).sent(AnswerCallbackQuery)[0].callback_query_id == "cb-1"
    assert session_of(adapter).sent(SendMessage)[-1].text == "Done"


async def test_the_callback_is_acknowledged_before_the_turn_and_a_refused_ack_still_sends(
    adapter: TelegramAdapter, harness: FakeHarness, caplog: pytest.LogCaptureFixture
) -> None:
    """Telegram forgets a callback query within seconds; the turn may take longer.

    Seen on the VPS: the ack after a 16 s turn raised `query is too old`, the
    handler aborted and the reply was never sent.
    """
    [(row_id, _)] = await store_buttons([Button("✅ Do it", "confirm")], HABITICA_USER)
    session = session_of(adapter)
    original = session.make_request
    order: list[str] = []

    async def refuse_ack(bot_: Any, method: Any, timeout: int | None = None) -> Any:
        if isinstance(method, AnswerCallbackQuery):
            order.append("ack")
            session.requests.append(method)
            raise TelegramBadRequest(
                method=method, message="Bad Request: query is too old and response timeout expired"
            )
        return await original(bot_, method, timeout)

    async def slow_button(user: User, action: str, payload: dict[str, Any]) -> Reply:
        order.append("turn")
        return Reply("Done")

    session.make_request = refuse_ack  # type: ignore[method-assign]
    harness.button_pressed = slow_button  # type: ignore[method-assign]
    await adapter.feed(callback_update(str(row_id)))

    assert order == ["ack", "turn"]
    assert session.sent(SendMessage)[-1].text == "Done"
    assert "could not be acknowledged" in caplog.text


async def test_unknown_callback_data_is_reported_as_expired(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    await adapter.feed(callback_update("123456"))

    assert harness.calls == [("button", (EXPIRED_BUTTON, {"data": "123456"}))]


async def test_a_button_stored_for_another_user_is_expired_too(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    [(row_id, _)] = await store_buttons([Button("x", "confirm")], "someone-else")

    await adapter.feed(callback_update(str(row_id)))

    assert harness.calls[0][1][0] == EXPIRED_BUTTON


async def test_a_voice_note_is_downloaded_and_handed_to_the_voice_entry_point(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    session_of(adapter).file_bytes = b"OggS\x00\x02opus"
    update = message_update("")
    del update["message"]["text"]
    update["message"]["voice"] = {
        "file_id": "voice-1",
        "file_unique_id": "u",
        "duration": 3,
        "mime_type": "audio/ogg",
    }

    await adapter.feed(update)

    assert harness.calls == [("voice", (b"OggS\x00\x02opus", "audio/ogg"))]
    (get_file,) = session_of(adapter).sent(GetFile)
    assert get_file.file_id == "voice-1"


async def test_a_voice_note_without_a_mime_type_is_taken_as_ogg(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    session_of(adapter).file_bytes = b"OggS"
    update = message_update("")
    del update["message"]["text"]
    update["message"]["voice"] = {"file_id": "voice-2", "file_unique_id": "u", "duration": 1}

    await adapter.feed(update)

    assert harness.calls == [("voice", (b"OggS", "audio/ogg"))]


async def test_non_text_messages_get_the_text_entry_point_with_nothing_to_say(
    adapter: TelegramAdapter, harness: FakeHarness
) -> None:
    update = message_update("")
    del update["message"]["text"]
    update["message"]["sticker"] = {
        "file_id": "f",
        "file_unique_id": "u",
        "type": "regular",
        "width": 1,
        "height": 1,
        "is_animated": False,
        "is_video": False,
    }

    await adapter.feed(update)

    assert harness.calls == [("text", "")]


async def test_outbound_goes_to_every_allowlisted_chat(
    adapter: TelegramAdapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter.allowed = {ALLOWED, 4343}

    await adapter.deliver(User(HABITICA_USER), Reply("You still have 2 dailies"))

    assert [s.chat_id for s in session_of(adapter).sent(SendMessage)] == [ALLOWED, 4343]


async def test_webhook_mode_sets_the_webhook_and_stop_closes_the_session(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, harness: FakeHarness
) -> None:
    configure(monkeypatch, mode="webhook")
    bot, session = recording_bot()
    adapter = TelegramAdapter(harness, get_settings(), bot=bot)

    await adapter.start()
    await adapter.stop()

    (hook,) = session.sent(SetWebhook)
    assert hook.url == f"https://bot.example.com/webhooks/telegram/{SECRET}"
    assert session.closed is True
    get_settings.cache_clear()


async def test_polling_mode_drops_the_webhook_and_polls_until_stopped(
    adapter: TelegramAdapter,
) -> None:
    await adapter.start()
    await asyncio.sleep(0.05)
    await adapter.stop()

    session = session_of(adapter)
    assert len(session.sent(DeleteWebhook)) == 1
    assert session.sent(SetWebhook) == []
    assert len(session.sent(GetUpdates)) >= 1
    assert adapter.polling is None and session.closed is True


async def test_adapter_starts_only_with_telegram_and_habitica_configured(
    db_path: Path, monkeypatch: pytest.MonkeyPatch, harness: FakeHarness
) -> None:
    assert start_telegram_if_configured(harness) is None  # no Telegram settings at all

    configure(monkeypatch, mode="webhook")
    assert start_telegram_if_configured(None) is None  # Telegram yes, Habitica no

    adapter = start_telegram_if_configured(harness)
    assert adapter is not None and adapter.mode == "webhook"
    assert adapter.allowed == {ALLOWED} and adapter.user == User(HABITICA_USER)
    await adapter.bot.session.close()
    get_settings.cache_clear()


# ---- the webhook route ----


@pytest.fixture
async def web(db_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    configure(monkeypatch, mode="webhook")
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.state.telegram = None
    get_settings.cache_clear()


async def test_wrong_secret_is_forbidden(web: httpx.AsyncClient) -> None:
    response = await web.post("/webhooks/telegram/wrong", json=message_update("/stats"))

    assert response.status_code == 403


async def test_without_a_running_adapter_the_route_is_unavailable(web: httpx.AsyncClient) -> None:
    from app.main import app

    app.state.telegram = None

    response = await web.post(f"/webhooks/telegram/{SECRET}", json=message_update("/stats"))

    assert response.status_code == 503


async def test_a_good_update_reaches_the_harness(web: httpx.AsyncClient) -> None:
    from app.main import app

    harness = FakeHarness()
    bot, session = recording_bot()
    app.state.telegram = TelegramAdapter(harness, get_settings(), bot=bot)

    response = await web.post(f"/webhooks/telegram/{SECRET}", json=message_update("/stats"))

    assert response.status_code == 200
    assert harness.calls == [("command", ("stats", ""))]
    assert session.sent(SendMessage)[0].text == "ok"
