"""The startup self-check: one line per thing that is missing, with fakes."""

from collections.abc import AsyncIterator

import httpx
import pytest
from aiogram.types import WebhookInfo

from app.adapters.telegram.bot import TelegramAdapter
from app.config import Settings, get_settings
from app.habitica.types import Webhook
from app.selfcheck import run_selfcheck
from tests.fakes.habitica import FakeHabiticaClient
from tests.fakes.telegram import RecordingSession, recording_bot
from tests.test_telegram import FakeHarness, configure

BASE = "https://bot.example.com"
SECRET = "s3cret"
OUR_URL = f"{BASE}/webhooks/habitica/{SECRET}"
TELEGRAM_URL = f"{BASE}/webhooks/telegram/{SECRET}"


def settings_with(monkeypatch: pytest.MonkeyPatch, base_url: str, secret: str = SECRET) -> Settings:
    # Webhook mode refuses to boot without both (config validation), so the
    # half-configured cases can only happen in polling mode.
    configure(monkeypatch, mode="webhook" if base_url and secret else "polling")
    monkeypatch.setenv("PUBLIC_BASE_URL", base_url)
    monkeypatch.setenv("WEBHOOK_SECRET", secret)
    get_settings.cache_clear()
    return get_settings()


def health_ok(request: httpx.Request) -> httpx.Response:
    assert request.url == f"{BASE}/health"
    return httpx.Response(200, json={"ok": True, "db": True})


def http_answering(handler) -> httpx.AsyncClient:  # noqa: ANN001 - a MockTransport handler
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def hook(url: str, enabled: bool = True) -> Webhook:
    return Webhook(id="w1", url=url, type="taskActivity", enabled=enabled, options={})


@pytest.fixture
async def telegram(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[TelegramAdapter]:
    """An adapter in webhook mode that has already set its webhook."""
    settings_with(monkeypatch, BASE)
    bot, _session = recording_bot()
    adapter = TelegramAdapter(FakeHarness(), get_settings(), bot=bot)
    await adapter.start()
    yield adapter
    await adapter.stop()
    get_settings.cache_clear()


def session_of(adapter: TelegramAdapter) -> RecordingSession:
    assert isinstance(adapter.bot.session, RecordingSession)
    return adapter.bot.session


# ---- configuration ----


async def test_no_public_url_is_reported_and_nothing_is_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with(monkeypatch, "")
    habitica = FakeHabiticaClient()

    result = await run_selfcheck(settings, habitica, None, http_answering(health_ok))

    assert not result.ok
    assert len(result.problems) == 1
    assert "PUBLIC_BASE_URL" in result.problems[0]
    assert habitica.calls == []


async def test_no_secret_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, BASE, secret="")
    habitica = FakeHabiticaClient()

    result = await run_selfcheck(settings, habitica, None, http_answering(health_ok))

    assert [p for p in result.problems if "WEBHOOK_SECRET" in p]
    assert habitica.calls == []


# ---- Habitica webhook ----


async def test_missing_habitica_webhook_is_created(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, BASE)
    habitica = FakeHabiticaClient()

    result = await run_selfcheck(settings, habitica, None, http_answering(health_ok))

    assert result.ok
    assert habitica.called("create_webhook") == [OUR_URL]
    assert any("created" in line for line in result.actions)


async def test_stale_habitica_webhook_is_moved_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, BASE)
    habitica = FakeHabiticaClient()
    habitica.webhooks.append(hook("https://old.example.com/webhooks/habitica/oldsecret"))

    result = await run_selfcheck(settings, habitica, None, http_answering(health_ok))

    assert result.ok
    assert habitica.called("update_webhook") == [("w1", OUR_URL)]
    assert habitica.called("create_webhook") == []
    assert [h.url for h in habitica.webhooks] == [OUR_URL]
    assert any("moved" in line for line in result.actions)


async def test_disabled_habitica_webhook_is_enabled_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with(monkeypatch, BASE)
    habitica = FakeHabiticaClient()
    habitica.webhooks.append(hook(OUR_URL, enabled=False))

    result = await run_selfcheck(settings, habitica, None, http_answering(health_ok))

    assert result.ok
    assert habitica.called("update_webhook") == [("w1", OUR_URL)]
    assert habitica.webhooks[0].enabled is True
    assert any("enabled" in line for line in result.actions)


async def test_registered_habitica_webhook_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, BASE)
    habitica = FakeHabiticaClient()
    habitica.webhooks.append(hook(OUR_URL))
    habitica.webhooks.append(hook("https://example.org/somebody-elses-hook"))

    result = await run_selfcheck(settings, habitica, None, http_answering(health_ok))

    assert result.ok
    assert habitica.called("update_webhook") == []
    assert habitica.called("create_webhook") == []


# ---- reachability ----


async def test_unreachable_app_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, BASE)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = await run_selfcheck(settings, None, None, http_answering(refused))

    assert [p for p in result.problems if "/health" in p and "unreachable" in p]


async def test_wrong_answer_at_public_url_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = settings_with(monkeypatch, BASE)

    def default_nginx_page(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>Welcome to nginx!</html>")

    result = await run_selfcheck(settings, None, None, http_answering(default_nginx_page))

    assert [p for p in result.problems if "/health" in p and "nginx" in p]


async def test_unreachable_app_does_not_stop_the_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = settings_with(monkeypatch, BASE)
    habitica = FakeHabiticaClient()

    def gateway_error(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    result = await run_selfcheck(settings, habitica, None, http_answering(gateway_error))

    assert [p for p in result.problems if "502" in p]
    assert habitica.called("create_webhook") == [OUR_URL]


# ---- Telegram webhook ----


async def test_telegram_aimed_at_us_passes(telegram: TelegramAdapter) -> None:
    result = await run_selfcheck(get_settings(), None, telegram, http_answering(health_ok))

    assert result.ok


async def test_telegram_aimed_elsewhere_is_reported(telegram: TelegramAdapter) -> None:
    session_of(telegram).webhook_info = WebhookInfo(
        url="https://old.example.com/webhooks/telegram/x",
        has_custom_certificate=False,
        pending_update_count=0,
    )

    result = await run_selfcheck(get_settings(), None, telegram, http_answering(health_ok))

    assert len(result.problems) == 1
    assert "Telegram webhook is https://old.example.com" in result.problems[0]
    assert TELEGRAM_URL in result.problems[0]


async def test_telegram_delivery_error_is_reported(telegram: TelegramAdapter) -> None:
    session_of(telegram).webhook_info = WebhookInfo(
        url=TELEGRAM_URL,
        has_custom_certificate=False,
        pending_update_count=7,
        last_error_date=1_760_000_000,
        last_error_message="SSL error {error:0A000086:SSL routines::certificate verify failed}",
    )

    result = await run_selfcheck(get_settings(), None, telegram, http_answering(health_ok))

    assert len(result.problems) == 1
    assert "certificate verify failed" in result.problems[0]
    assert "7 updates pending" in result.problems[0]


async def test_polling_adapter_has_no_webhook_to_check(monkeypatch: pytest.MonkeyPatch) -> None:
    settings_with(monkeypatch, BASE)
    monkeypatch.setenv("TELEGRAM_MODE", "polling")
    get_settings.cache_clear()
    bot, _session = recording_bot()
    adapter = TelegramAdapter(FakeHarness(), get_settings(), bot=bot)

    assert await adapter.check_webhook() is None
