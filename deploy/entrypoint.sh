#!/usr/bin/env sh
# Migrate, then serve. Same two steps as scripts/dev.sh, in webhook mode.
set -eu

cd /app
alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
