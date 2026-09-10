import pytest
from pydantic import ValidationError

from app.config import MissingSettings, Settings, get_settings


def make_settings(**overrides: object) -> Settings:
    """Ignore any .env the developer happens to have; the tests set the environment."""
    return Settings(_env_file=None, **overrides)


def test_defaults_do_not_need_any_environment() -> None:
    settings = make_settings()

    assert settings.database_url == "sqlite+aiosqlite:///./data/analytica.db"
    assert settings.timezone == "Europe/Zurich"
    assert settings.telegram_mode == "polling"
    assert settings.llm_strong_model == "z-ai/glm-5.3"


def test_values_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIMEZONE", "Europe/Paris")
    monkeypatch.setenv("SESSION_TOKEN_BUDGET", "1000")
    monkeypatch.setenv("HABITICA_USER_ID", "abc")

    settings = make_settings()

    assert settings.timezone == "Europe/Paris"
    assert settings.session_token_budget == 1000
    assert settings.habitica_user_id == "abc"


def test_allowed_user_ids_are_split_and_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "123, 456")

    assert make_settings().telegram_allowed_user_ids == [123, 456]


def test_empty_allowlist_is_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_ALLOWED_USER_IDS", "")

    assert make_settings().telegram_allowed_user_ids == []


def test_a_missing_group_only_fails_when_that_group_is_needed() -> None:
    settings = make_settings()

    settings.require_google()  # defaults are filled in, so this group is fine

    with pytest.raises(MissingSettings) as error:
        settings.require_habitica()

    message = str(error.value)
    assert "Habitica" in message
    assert "HABITICA_USER_ID" in message
    assert "HABITICA_API_TOKEN" in message
    assert ".env.example" in message


def test_a_filled_in_group_does_not_raise() -> None:
    settings = make_settings(habitica_user_id="u", habitica_api_token="t")

    settings.require_habitica()


def test_telegram_group_needs_both_token_and_allowlist() -> None:
    settings = make_settings(telegram_bot_token="t")

    with pytest.raises(MissingSettings, match="TELEGRAM_ALLOWED_USER_IDS"):
        settings.require_telegram()


def test_webhook_mode_requires_a_public_url_and_secret() -> None:
    with pytest.raises(ValidationError, match="PUBLIC_BASE_URL, WEBHOOK_SECRET"):
        make_settings(telegram_mode="webhook")


def test_webhook_mode_is_fine_once_both_are_set() -> None:
    settings = make_settings(
        telegram_mode="webhook",
        public_base_url="https://bot.example.com",
        webhook_secret="s3cret",
    )

    assert settings.telegram_mode == "webhook"


def test_get_settings_is_cached() -> None:
    get_settings.cache_clear()
    try:
        assert get_settings() is get_settings()
    finally:
        get_settings.cache_clear()
