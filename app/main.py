"""The single process: a FastAPI app whose lifespan owns everything else.

For now the lifespan only configures logging and disposes the engine. The
Telegram adapter (P02) and the scheduler (P01) hang off the same hook.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.collector import webhooks
from app.config import get_settings
from app.db.engine import dispose_engine, session_scope

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger.info("analytica starting up")
    try:
        yield
    finally:
        await dispose_engine()
        logger.info("analytica shut down")


app = FastAPI(title="Habitica Analytica", lifespan=lifespan)
app.include_router(webhooks.router)


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
