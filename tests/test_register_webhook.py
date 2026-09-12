import pytest

from app.config import get_settings
from scripts import register_webhook
from tests.fakes.habitica import FakeHabiticaClient


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> FakeHabiticaClient:
    client = FakeHabiticaClient()

    class Factory:
        async def __aenter__(self) -> FakeHabiticaClient:
            return client

        async def __aexit__(self, *exc: object) -> None:
            pass

    monkeypatch.setattr(register_webhook, "client_from_settings", lambda: Factory())
    return client


def configure(monkeypatch: pytest.MonkeyPatch, base_url: str, secret: str = "s3cret") -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", base_url)
    monkeypatch.setenv("WEBHOOK_SECRET", secret)
    get_settings.cache_clear()


async def test_no_public_url_means_nothing_to_do(
    monkeypatch: pytest.MonkeyPatch, fake_client: FakeHabiticaClient
) -> None:
    configure(monkeypatch, "")

    await register_webhook.main()

    assert fake_client.calls == []


def test_missing_secret_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch, "https://bot.example.com", secret="")

    with pytest.raises(SystemExit):
        register_webhook.webhook_url()


async def test_registers_once(
    monkeypatch: pytest.MonkeyPatch, fake_client: FakeHabiticaClient
) -> None:
    configure(monkeypatch, "https://bot.example.com/")

    await register_webhook.main()
    await register_webhook.main()

    assert fake_client.called("create_webhook") == [
        "https://bot.example.com/webhooks/habitica/s3cret"
    ]
    assert len(fake_client.called("list_webhooks")) == 2
