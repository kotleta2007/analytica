"""Slash commands. Each one is a function of the turn and its arguments.

    /start, /help, /language [en|ru|fr], /stats, /sync

Later phases add theirs to `COMMANDS`. Free text is not a command and never
lands here.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

import httpx

from app.collector.snapshots import collect, latest_user_snapshot
from app.core.i18n import LANGUAGES, t
from app.core.replies import Reply
from app.core.turn import Turn
from app.db.models import UserSnapshot
from app.habitica.client import HabiticaError
from app.habitica.types import user_stats_from_json

logger = logging.getLogger(__name__)

# A snapshot older than this is refreshed before /stats shows it.
STATS_MAX_AGE = timedelta(minutes=10)

Handler = Callable[[Turn, str], Awaitable[Reply]]


def parse_command(text: str) -> tuple[str, str] | None:
    """`"/language fr"` → `("language", "fr")`; `"/start@MyBot"` → `("start", "")`.

    `None` for anything that is not a command.
    """
    stripped = text.strip()
    if not stripped.startswith("/") or len(stripped) == 1:
        return None
    head, _, args = stripped[1:].partition(" ")
    command = head.split("@", 1)[0].lower()
    if not command:
        return None
    return command, args.strip()


async def run(turn: Turn, command: str, args: str) -> Reply:
    handler = COMMANDS.get(command)
    if handler is None:
        return Reply(t("unknown_command", turn.language, command=command))
    try:
        return await handler(turn, args)
    except (HabiticaError, httpx.HTTPError) as error:
        logger.warning("/%s: Habitica call failed: %s", command, error)
        return Reply(t("habitica_error", turn.language, error=error))


async def start(turn: Turn, args: str) -> Reply:
    return Reply(t("welcome", turn.language) + "\n\n" + help_text(turn.language))


async def help_(turn: Turn, args: str) -> Reply:
    return Reply(help_text(turn.language))


def help_text(lang: str) -> str:
    lines = [t("help_header", lang)]
    for usage, key in HELP:
        lines.append(f"{usage} — {t(key, lang)}")
    return "\n".join(lines)


async def language(turn: Turn, args: str) -> Reply:
    if not args:
        return Reply(t("language_current", turn.language))
    wanted = args.lower()
    if wanted not in LANGUAGES:
        return Reply(t("language_invalid", turn.language))
    turn.settings.language = wanted
    await turn.session.flush()
    return Reply(t("language_set", wanted))


async def stats(turn: Turn, args: str) -> Reply:
    snapshot = await latest_user_snapshot(turn.session, turn.user.id)
    if snapshot is None or turn.now - snapshot.taken_at > STATS_MAX_AGE:
        outcome = await collect(turn.habitica, turn.session, turn.user.id, turn.now)
        snapshot = outcome.user
    return Reply(stats_line(turn, snapshot))


async def sync(turn: Turn, args: str) -> Reply:
    outcome = await collect(turn.habitica, turn.session, turn.user.id, turn.now)
    summary = t(
        "sync_done",
        turn.language,
        tasks=outcome.tasks.seen,
        changed=outcome.tasks.changed,
        events=len(outcome.tasks.events),
    )
    return Reply(summary + "\n" + stats_line(turn, outcome.user))


def stats_line(turn: Turn, snapshot: UserSnapshot) -> str:
    # The row's raw column is the whole /user object; the parser knows the maxima.
    user = user_stats_from_json(snapshot.raw)
    return t(
        "stats_line",
        turn.language,
        hp=fmt(user.hp),
        max_hp=user.max_hp,
        mp=fmt(user.mp),
        max_mp=user.max_mp,
        exp=fmt(user.exp),
        to_next_level=user.to_next_level,
        gp=fmt(user.gp),
        lvl=user.lvl,
        class_name=user.class_name,
        time=snapshot.taken_at.astimezone(turn.tz).strftime("%H:%M"),
    )


def fmt(value: float) -> str:
    """Habitica stats are floats; `49.0` reads as `49`, `42.7` stays `42.7`."""
    return f"{value:.0f}" if value == int(value) else f"{value:.1f}"


COMMANDS: dict[str, Handler] = {
    "start": start,
    "help": help_,
    "language": language,
    "stats": stats,
    "sync": sync,
}

HELP: list[tuple[str, str]] = [
    ("/help", "help_help"),
    ("/language en|ru|fr", "help_language"),
    ("/stats", "help_stats"),
    ("/sync", "help_sync"),
]
