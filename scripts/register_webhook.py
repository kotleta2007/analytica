"""Register our taskActivity webhook with Habitica by hand.

    uv run python scripts/register_webhook.py

The app does the same on every startup (app/selfcheck.py); this is for
checking the registration without starting it. Needs PUBLIC_BASE_URL and
WEBHOOK_SECRET in .env. With no PUBLIC_BASE_URL it says so and exits 0:
without a public URL there is nothing to register and snapshots carry the
load.
"""

import asyncio
import logging
import sys

from app.collector.webhooks import ensure_webhook, receiver_url
from app.config import get_settings
from app.habitica.client import client_from_settings

logger = logging.getLogger("register_webhook")


def webhook_url() -> str | None:
    settings = get_settings()
    if not settings.public_base_url:
        logger.info("PUBLIC_BASE_URL is empty; no webhook to register yet")
        return None
    if not settings.webhook_secret:
        logger.error("WEBHOOK_SECRET is empty; set it before registering (openssl rand -hex 24)")
        sys.exit(1)
    return receiver_url(settings.public_base_url, settings.webhook_secret)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    url = webhook_url()
    if url is None:
        return
    async with client_from_settings() as client:
        logger.info(await ensure_webhook(client, url))


if __name__ == "__main__":
    asyncio.run(main())
