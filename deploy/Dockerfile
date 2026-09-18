# Habitica Analytica on the VPS: one container, the app only.
#
#   docker compose -f deploy/docker-compose.yml up -d --build
#
# The host's nginx terminates TLS and proxies to 127.0.0.1:8000 (see
# nginx-analytica.conf). Nothing in here listens on 80 or 443. Plain
# Dockerfile syntax on purpose: it builds with or without BuildKit.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Byte-compile on install (faster cold start), never download another Python
# (the image has one), and put the venv and the repo on the path.
ENV UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=0 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencies first, from the lockfile only, so a code change reuses this layer.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

COPY . /app

# `data/` is the bind-mounted volume with the database, the content cache and
# the backups; the container runs as uid 1000, so the host directory must be
# writable by that uid (see the README).
RUN useradd --uid 1000 --user-group --create-home analytica \
    && mkdir -p /app/data \
    && chown -R analytica:analytica /app
USER analytica

VOLUME ["/app/data"]
EXPOSE 8000

ENTRYPOINT ["/app/deploy/entrypoint.sh"]
