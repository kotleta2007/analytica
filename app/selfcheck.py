"""Startup self-check: is the outside world wired to this deployment?

Runs once, a few seconds after startup — uvicorn accepts connections only
after the lifespan has yielded, so the reachability check cannot run inside
it. Three questions, each answered with one log line the operator can act
on; nothing here stops the app:

- does `PUBLIC_BASE_URL/health` answer like us through DNS, nginx and TLS?
- does Habitica post task activity to our receiver? (registered here if not)
- is Telegram's webhook aimed at us, and is it getting through?
"""

import asyncio
import logging
from dataclasses import dataclass, field

import httpx
from aiogram.exceptions import AiogramError

from app.adapters.telegram.bot import TelegramAdapter
from app.collector.webhooks import ensure_webhook, receiver_url
from app.config import Settings
from app.habitica.client import HabiticaClient, HabiticaError

logger = logging.getLogger(__name__)

STARTUP_DELAY_SECONDS = 3.0
HEALTH_TIMEOUT_SECONDS = 10.0


@dataclass
class SelfCheck:
    problems: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


async def run_after_startup(
    settings: Settings, habitica: HabiticaClient | None, telegram: TelegramAdapter | None
) -> None:
    """The lifespan's background task: wait for the server, check, log."""
    await asyncio.sleep(STARTUP_DELAY_SECONDS)
    try:
        await run_selfcheck(settings, habitica, telegram)
    except Exception:
        logger.exception("self-check crashed")


async def run_selfcheck(
    settings: Settings,
    habitica: HabiticaClient | None,
    telegram: TelegramAdapter | None,
    http: httpx.AsyncClient | None = None,
) -> SelfCheck:
    result = SelfCheck()
    if not settings.public_base_url:
        result.problems.append(
            "PUBLIC_BASE_URL is empty: Habitica webhooks are not registered "
            "(snapshots carry the load) and Telegram can only poll"
        )
    elif not settings.webhook_secret:
        result.problems.append(
            "WEBHOOK_SECRET is empty: webhooks cannot be registered (openssl rand -hex 24)"
        )
    else:
        await check_wiring(settings, habitica, telegram, http, result)
    for line in result.actions:
        logger.info("self-check: %s", line)
    for line in result.problems:
        logger.warning("self-check: %s", line)
    if result.ok:
        logger.info(
            "self-check passed: %s is reachable and both webhooks point at it",
            settings.public_base_url,
        )
    return result


async def check_wiring(
    settings: Settings,
    habitica: HabiticaClient | None,
    telegram: TelegramAdapter | None,
    http: httpx.AsyncClient | None,
    result: SelfCheck,
) -> None:
    problem = await check_reachable(settings.public_base_url, http)
    if problem:
        result.problems.append(problem)
    if habitica is not None:
        url = receiver_url(settings.public_base_url, settings.webhook_secret)
        try:
            result.actions.append(await ensure_webhook(habitica, url))
        except (HabiticaError, httpx.HTTPError) as error:
            result.problems.append(f"could not register the Habitica webhook: {error}")
    if telegram is not None:
        try:
            problem = await telegram.check_webhook()
        except AiogramError as error:
            problem = f"could not read the Telegram webhook state: {error}"
        if problem:
            result.problems.append(problem)


async def check_reachable(public_base_url: str, http: httpx.AsyncClient | None) -> str | None:
    """GET our own /health through the public URL; None when it answers like us."""
    url = f"{public_base_url.rstrip('/')}/health"
    try:
        if http is None:
            async with httpx.AsyncClient(timeout=HEALTH_TIMEOUT_SECONDS) as own:
                response = await own.get(url)
        else:
            response = await http.get(url)
    except httpx.HTTPError as error:
        return f"{url} is unreachable ({type(error).__name__}: {error}); webhooks cannot arrive"
    if response.status_code != 200:
        return f"{url} answered {response.status_code}; is nginx proxying to the app?"
    try:
        answered_like_us = response.json().get("ok") is True
    except (ValueError, AttributeError):
        answered_like_us = False
    if not answered_like_us:
        return f"{url} did not answer like the app; is nginx proxying to the right port?"
    return None
