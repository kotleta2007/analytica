"""Settings for every phase, in one place.

Validation is lazy and grouped: the app boots with whatever credentials are
filled in, and a group's `require_*` accessor raises a clear error the first
time code that actually needs those credentials runs.
"""

import re
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class MissingSettings(RuntimeError):
    """A group of settings is needed now and is not filled in."""


# The secret is a path segment of both webhook URLs; RFC 3986 unreserved
# characters need no escaping anywhere.
URL_SAFE = re.compile(r"^[A-Za-z0-9._~-]*$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- core (phase 00) ----
    database_url: str = "sqlite+aiosqlite:///./data/analytica.db"
    timezone: str = "Europe/Zurich"
    log_level: str = "INFO"

    # ---- Habitica (phase 01) ----
    habitica_user_id: str = ""
    habitica_api_token: str = ""

    # ---- Telegram (phase 02) ----
    telegram_bot_token: str = ""
    # NoDecode: the value in .env is comma-separated, not JSON.
    telegram_allowed_user_ids: Annotated[list[int], NoDecode] = []
    telegram_mode: Literal["polling", "webhook"] = "polling"

    # ---- public URL + webhooks ----
    public_base_url: str = ""
    webhook_secret: str = ""

    # ---- models, all via OpenRouter (phases 03-04) ----
    openrouter_api_key: str = ""
    llm_strong_model: str = "z-ai/glm-5.3"
    llm_cheap_model: str = "deepseek/deepseek-v4-flash"
    # Tried in order when the chosen model fails: OpenRouter drops models without notice.
    llm_fallback_models: Annotated[list[str], NoDecode] = ["google/gemini-3.5-flash"]
    asr_model: str = "qwen/qwen3-asr-1.7b"
    asr_fallback_models: Annotated[list[str], NoDecode] = [
        "openai/gpt-4o-mini-transcribe",
        "mistralai/voxtral-mini-transcribe-3-0",
    ]
    tts_model: str = "fish-audio/s2.1-pro"
    tts_voice: str = "b347db033a6549378b48d00acb0d06cd"

    # ---- sessions and context (phase 03) ----
    session_idle_quick_min: int = 15
    session_idle_planning_min: int = 120
    session_token_budget: int = 24000

    # ---- schedule (phase 05) ----
    reminder_time: str = "21:00"
    weekly_report: str = "sun 19:00"
    # How long after day start our cron waits for the "still unticked" answer, at most.
    cron_grace_min: int = 90

    # ---- Google Calendar (phase 09) ----
    google_client_secrets_path: str = "./data/google_client_secrets.json"
    google_token_path: str = "./data/google_token.json"
    plan_hours: str = "09:00-19:00"
    plan_slot_minutes: int = 60
    plan_checkin_delay_min: int = 10

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def _split_user_ids(cls, value: object) -> object:
        """Accept the comma-separated form used in .env."""
        if not isinstance(value, str):
            return value
        return [int(part) for part in value.split(",") if part.strip()]

    @field_validator("llm_fallback_models", "asr_fallback_models", mode="before")
    @classmethod
    def _split_model_ids(cls, value: object) -> object:
        """Comma-separated in .env; empty turns the fallback off."""
        if not isinstance(value, str):
            return value
        return [part.strip() for part in value.split(",") if part.strip()]

    @field_validator("webhook_secret")
    @classmethod
    def _secret_is_url_safe(cls, value: str) -> str:
        # `WEBHOOK_SECRET=$(openssl rand -hex 24)` pasted literally is the
        # mistake this catches: dotenv runs no shell, and Habitica rejects
        # the resulting URL with a bare 400.
        if not URL_SAFE.match(value):
            raise ValueError(
                "WEBHOOK_SECRET must contain only letters, digits and . _ ~ - "
                "(it is part of the webhook URLs); paste the output of: openssl rand -hex 24"
            )
        return value

    @model_validator(mode="after")
    def _webhook_mode_needs_a_public_url(self) -> "Settings":
        if self.telegram_mode == "webhook":
            missing = [
                name for name in ("public_base_url", "webhook_secret") if not getattr(self, name)
            ]
            if missing:
                raise ValueError(
                    "TELEGRAM_MODE=webhook requires " + ", ".join(n.upper() for n in missing)
                )
        return self

    def require_habitica(self) -> None:
        self._require(("habitica_user_id", "habitica_api_token"), "Habitica")

    def require_telegram(self) -> None:
        self._require(("telegram_bot_token", "telegram_allowed_user_ids"), "Telegram")

    def require_openrouter(self) -> None:
        self._require(("openrouter_api_key",), "OpenRouter")

    def require_google(self) -> None:
        self._require(("google_client_secrets_path", "google_token_path"), "Google Calendar")

    def _require(self, names: tuple[str, ...], group: str) -> None:
        missing = [name.upper() for name in names if not getattr(self, name)]
        if missing:
            raise MissingSettings(
                f"{group} is not configured: set {', '.join(missing)} in .env (see .env.example)"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
