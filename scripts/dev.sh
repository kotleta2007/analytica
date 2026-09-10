#!/usr/bin/env bash
# Local development: migrate, then run the app in polling mode.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
    echo "No .env found. Copy .env.example to .env and fill it in." >&2
    exit 1
fi

export TELEGRAM_MODE=polling

uv run alembic upgrade head
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
