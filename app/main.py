"""The single process: a FastAPI app whose lifespan owns everything else.

The lifespan configures logging, builds one Habitica client and the
OpenRouter clients (chat, speech in, speech out), starts the collector
schedule and the Telegram adapter when their credentials are present, kicks
off the deployment self-check, and disposes the engine at the end.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import selfcheck
from app.adapters.telegram import bot as telegram
from app.collector import webhooks
from app.config import get_settings
from app.core.harness import Harness
from app.db.engine import dispose_engine, session_scope
from app.habitica.client import HabiticaClient
from app.jobs.scheduler import build_collector
from app.llm.client import client_from_settings as llm_client_from_settings
from app.logs import configure_logging
from app.speech.asr import client_from_settings as asr_client_from_settings
from app.speech.tts import client_from_settings as tts_client_from_settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings)
    logger.info("analytica starting up")
    client = habitica_client_if_configured()
    llm = llm_client_from_settings()
    asr = asr_client_from_settings()
    tts = tts_client_from_settings()
    collector = None if client is None else build_collector(client, settings.habitica_user_id)
    harness = None if client is None else Harness(habitica=client, llm=llm, asr=asr, tts=tts)
    adapter = telegram.start_telegram_if_configured(harness)
    app.state.telegram = adapter
    if collector is not None:
        await collector.start()
    if adapter is not None:
        await adapter.start()
    checker = asyncio.create_task(selfcheck.run_after_startup(settings, client, adapter))
    try:
        yield
    finally:
        checker.cancel()
        with suppress(asyncio.CancelledError):
            await checker  # before the clients it may be using are closed
        if adapter is not None:
            await adapter.stop()
        if collector is not None:
            await collector.stop()  # also closes the shared Habitica client
        for openrouter_client in (llm, asr, tts):
            if openrouter_client is not None:
                await openrouter_client.aclose()
        app.state.telegram = None
        await dispose_engine()
        logger.info("analytica shut down")


def habitica_client_if_configured() -> HabiticaClient | None:
    settings = get_settings()
    if not (settings.habitica_user_id and settings.habitica_api_token):
        logger.warning("Habitica is not configured; the collector and the bot are off")
        return None
    return HabiticaClient(settings.habitica_user_id, settings.habitica_api_token)


app = FastAPI(title="Habitica Analytica", lifespan=lifespan)
app.include_router(webhooks.router)
app.include_router(telegram.router)


async def database_reachable() -> bool:
    try:
        async with session_scope() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("health check could not reach the database")
        return False
    return True


@app.get("/health")
async def health() -> JSONResponse:
    db = await database_reachable()
    return JSONResponse({"ok": db, "db": db}, status_code=200 if db else 503)
