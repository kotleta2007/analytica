"""Register our taskActivity webhook with Habitica, once.

    uv run python scripts/register_webhook.py

Needs PUBLIC_BASE_URL and WEBHOOK_SECRET in .env. With no PUBLIC_BASE_URL it
says so and exits 0: until the VPS exists (phase 05) there is nothing to
register and snapshots carry the load.
"""

import asyncio
import logging
import sys

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
    return f"{settings.public_base_url.rstrip('/')}/webhooks/habitica/{settings.webhook_secret}"


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    url = webhook_url()
    if url is None:
        return
    async with client_from_settings() as client:
        for hook in await client.list_webhooks():
            if hook.url == url:
                logger.info("webhook already registered (id %s)", hook.id)
                return
        hook = await client.create_webhook(url)
    logger.info("registered webhook %s for %s", hook.id, url)


if __name__ == "__main__":
    asyncio.run(main())
