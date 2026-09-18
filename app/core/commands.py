"""Slash commands. Each one is a function of the turn and its arguments.

    /start, /help, /language [en|ru|fr], /stats, /sync, /new, /session, /model, /glossary, /tts

Later phases add theirs to `COMMANDS`. Free text is not a command and never
lands here.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import func, select

from app.collector.snapshots import collect, collect_if_stale
from app.core import gear, glossary, sessions, voice
from app.core.i18n import LANGUAGES, parse_clock, t
from app.core.replies import Reply
from app.core.skills import available_skills
from app.core.turn import Turn, summarizer
from app.db.models import LlmCall, UserSnapshot
from app.habitica.client import HabiticaError
from app.habitica.types import class_label, user_stats_from_json
from app.llm.models import MODEL_NAMES, registry

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
    snapshot = await collect_if_stale(
        turn.habitica, turn.session, turn.user.id, turn.now, STATS_MAX_AGE
    )
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


async def new(turn: Turn, args: str) -> Reply:
    chat = await sessions.open_session_for(turn.session, turn.user.id)
    if chat is None:
        return Reply(t("session_none", turn.language))
    await sessions.close_session(turn.session, chat, turn.now, summarizer(turn))
    return Reply(t("session_closed", turn.language))


async def session(turn: Turn, args: str) -> Reply:
    kind, _, topic = args.partition(" ")
    if kind.lower() == sessions.PLANNING:
        if not topic.strip():
            return Reply(t("session_planning_needs_topic", turn.language))
        open_chat = await sessions.open_session_for(turn.session, turn.user.id)
        if open_chat is not None:
            await sessions.close_session(turn.session, open_chat, turn.now, summarizer(turn))
        await sessions.open_session(
            turn.session, turn.user.id, turn.now, sessions.PLANNING, topic.strip()
        )
        return Reply(t("session_planning_opened", turn.language, topic=topic.strip()))
    chat = await sessions.open_session_for(turn.session, turn.user.id)
    if chat is None or sessions.is_idle(chat, turn.now):
        return Reply(t("session_none", turn.language))
    return Reply(
        t(
            "session_info",
            turn.language,
            kind=t(f"session_kind_{chat.kind}", turn.language),
            topic=f" — {chat.topic}" if chat.topic else "",
            age=minutes_text(turn.now - chat.started_at),
            idle=minutes_text(turn.now - chat.last_activity_at),
            count=len(await sessions.session_messages(turn.session, chat)),
        )
    )


async def model(turn: Turn, args: str) -> Reply:
    wanted = args.strip().lower()
    if wanted:
        if wanted not in MODEL_NAMES:
            return Reply(t("model_invalid", turn.language))
        turn.settings.model = wanted
        await turn.session.flush()
        return Reply(t("model_set", turn.language, name=wanted, id=registry()[wanted]))
    usage = await llm_usage(turn)
    lines = [t("model_header", turn.language)]
    for name, model_id in registry().items():
        marker = "●" if name == turn.settings.model else " "
        lines.append(f"{marker} {name:<6} {model_id} — {usage_text(usage.get(model_id))}")
    lines.append(t("model_footer", turn.language))
    return Reply("\n".join(lines))


async def glossary_(turn: Turn, args: str) -> Reply:
    """`/glossary` lists; `/glossary add <term>` and `/glossary remove <term>` edit."""
    verb, _, term = args.partition(" ")
    verb = verb.lower()
    if not verb:
        terms = await glossary.list_terms(turn.session, turn.user.id)
        if not terms:
            return Reply(t("glossary_empty", turn.language))
        return Reply(t("glossary_list", turn.language, terms="\n".join(f"• {x}" for x in terms)))
    if verb not in ("add", "remove") or not term.strip():
        return Reply(t("glossary_usage", turn.language))
    try:
        if verb == "add":
            added = await glossary.add_term(
                turn.session, turn.user.id, term, glossary.FROM_COMMAND, turn.now
            )
            key = "glossary_added" if added else "glossary_exists"
        else:
            removed = await glossary.remove_term(turn.session, turn.user.id, term)
            key = "glossary_removed" if removed else "glossary_missing"
    except glossary.GlossaryTermError as problem:
        return Reply(t("glossary_bad_term", turn.language, reason=problem))
    return Reply(t(key, turn.language, term=glossary.normalise(term)))


async def tts(turn: Turn, args: str) -> Reply:
    wanted = args.strip().lower()
    if not wanted:
        return Reply(t(f"tts_{turn.settings.tts}", turn.language))
    if wanted not in (voice.TTS_ON, voice.TTS_OFF):
        return Reply(t("tts_usage", turn.language))
    if wanted == voice.TTS_ON and not turn.can_speak:
        return Reply(t("tts_not_configured", turn.language))
    turn.settings.tts = wanted
    await turn.session.flush()
    return Reply(t(f"tts_{wanted}", turn.language))


@dataclass
class ModelUsage:
    calls: int
    failed: int
    cost_usd: float
    avg_latency_ms: float


async def remind(turn: Turn, args: str) -> Reply:
    """`/remind` shows the time; `/remind HH:MM` sets it; `/remind off` stops it."""
    settings = turn.settings
    if not args:
        if settings.reminder_time is None:
            return Reply(t("remind_none", turn.language))
        return Reply(t("remind_current", turn.language, time=settings.reminder_time))
    if args.strip().lower() == "off":
        settings.reminder_time = None
        await turn.session.flush()
        return Reply(t("remind_off", turn.language))
    try:
        wanted = parse_clock(args)
    except ValueError:
        return Reply(t("remind_usage", turn.language))
    settings.reminder_time = wanted.strftime("%H:%M")
    settings.last_reminder_day = None  # a time set for later today should fire today
    await turn.session.flush()
    return Reply(t("remind_set", turn.language, time=settings.reminder_time))


async def gear_(turn: Turn, args: str) -> Reply:
    """`/gear` shows the sets; `/gear <stat> auto` builds one from owned gear;
    `/gear <stat> <slot> <key>` sets a slot; `/gear now <stat>` puts a set on."""
    words = args.split()
    content = await turn.habitica.get_content()
    if words[:1] == ["now"] and len(words) == 2 and words[1] in gear.STATS:
        return Reply(await gear_now(turn, content, words[1]))
    snapshot = await collect_if_stale(
        turn.habitica, turn.session, turn.user.id, turn.now, STATS_MAX_AGE
    )
    user = user_stats_from_json(snapshot.raw)
    if not words:
        return Reply(gear_overview(turn, content, user.equipped))
    stat = words[0]
    if stat not in gear.STATS or len(words) not in (2, 3):
        return Reply(t("gear_usage", turn.language))
    if len(words) == 2:
        if words[1] != "auto":
            return Reply(t("gear_usage", turn.language))
        chosen = gear.best_set(stat, user.owned_gear, content)
        setattr(turn.settings, f"gear_{stat}", chosen)
        await turn.session.flush()
        return Reply(gear_line(turn, content, stat, chosen, user.equipped))
    slot, key = words[1], words[2]
    if slot not in gear.SLOTS:
        return Reply(t("gear_bad_slot", turn.language, slot=slot, slots=", ".join(gear.SLOTS)))
    if key not in gear.gear_table(content):
        return Reply(t("gear_unknown_item", turn.language, item_key=key))
    if key not in user.owned_gear:
        return Reply(t("gear_not_owned", turn.language, item=gear.item_name(key, content)))
    chosen = {**gear.set_for(turn.settings, stat), slot: key}
    setattr(turn.settings, f"gear_{stat}", chosen)
    await turn.session.flush()
    return Reply(gear_line(turn, content, stat, chosen, user.equipped))


def gear_overview(turn: Turn, content: dict[str, Any], equipped: dict[str, str]) -> str:
    lines = [t("gear_header", turn.language)]
    for stat in gear.STATS:
        lines.append(gear_line(turn, content, stat, gear.set_for(turn.settings, stat), equipped))
    return "\n".join(lines)


def gear_line(
    turn: Turn,
    content: dict[str, Any],
    stat: str,
    gear_set: dict[str, str],
    equipped: dict[str, str],
) -> str:
    if not gear_set:
        return t("gear_unset", turn.language, stat=stat.upper())
    items = " · ".join(
        f"{slot}: {gear.item_name(gear_set[slot], content)}"
        for slot in gear.SLOTS
        if slot in gear_set
    )
    on = t("gear_on_now", turn.language) if gear.is_on(gear_set, equipped) else ""
    return t(
        "gear_line",
        turn.language,
        stat=stat.upper(),
        value=gear.set_value(stat, gear_set, content),
        on=on,
        items=items,
    )


async def gear_now(turn: Turn, content: dict[str, Any], stat: str) -> str:
    """Equip from a fresh snapshot, and snapshot again after: the endpoint toggles,
    so a stale view of what is on would take items off."""
    gear_set = gear.set_for(turn.settings, stat)
    if not gear_set:
        return t("gear_unset", turn.language, stat=stat.upper())
    outcome = await collect(turn.habitica, turn.session, turn.user.id, turn.now)
    equipped = user_stats_from_json(outcome.user.raw).equipped
    keys = await gear.equip_set(turn.habitica, equipped, gear_set)
    if not keys:
        return t("gear_already_on", turn.language, stat=stat.upper())
    await collect(turn.habitica, turn.session, turn.user.id, turn.now)
    names = ", ".join(gear.item_name(key, content) for key in keys)
    return t("gear_equipped", turn.language, stat=stat.upper(), items=names)


async def skills_(turn: Turn, args: str) -> Reply:
    """What the user can cast now, with the mana already compared."""
    snapshot = await collect_if_stale(
        turn.habitica, turn.session, turn.user.id, turn.now, STATS_MAX_AGE
    )
    user = user_stats_from_json(snapshot.raw)
    available = available_skills(user, await turn.habitica.get_content())
    lines = [
        t(
            "skills_header",
            turn.language,
            class_name=class_label(user.class_name),
            lvl=user.lvl,
            mp=fmt(user.mp),
            max_mp=user.max_mp,
        )
    ]
    if not available:
        lines.append(t("skills_none", turn.language))
    for item in available:
        lines.append(
            t(
                "skills_line",
                turn.language,
                mark="✅" if item.castable else "⏳",
                text=item.skill.text,
                mana=item.skill.mana,
                where=t(f"skill_target_{item.skill.target}", turn.language),
            )
        )
    return Reply("\n".join(lines))


async def llm_usage(turn: Turn) -> dict[str, ModelUsage]:
    """Per model id, over every call this user ever made."""
    rows = await turn.session.execute(
        select(
            LlmCall.model,
            func.count(),
            func.sum(func.iif(LlmCall.ok, 0, 1)),
            func.coalesce(func.sum(LlmCall.cost_usd), 0.0),
            func.avg(LlmCall.latency_ms),
        )
        .where(LlmCall.user_id == turn.user.id)
        .group_by(LlmCall.model)
    )
    return {
        model_id: ModelUsage(calls, failed, cost, avg_latency)
        for model_id, calls, failed, cost, avg_latency in rows.all()
    }


def usage_text(usage: ModelUsage | None) -> str:
    if usage is None or usage.calls == 0:
        return "no calls yet"
    calls = f"{usage.calls} calls" if usage.calls != 1 else "1 call"
    if usage.failed:
        calls += f" ({usage.failed} failed)"
    return f"{calls}, ${usage.cost_usd:.4f}, avg {usage.avg_latency_ms / 1000:.1f} s"


def minutes_text(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


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
        class_name=class_label(user.class_name),
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
    "new": new,
    "session": session,
    "model": model,
    "glossary": glossary_,
    "tts": tts,
    "remind": remind,
    "gear": gear_,
    "skills": skills_,
}

HELP: list[tuple[str, str]] = [
    ("/help", "help_help"),
    ("/language en|ru|fr", "help_language"),
    ("/stats", "help_stats"),
    ("/sync", "help_sync"),
    ("/new", "help_new"),
    ("/session [planning <topic>]", "help_session"),
    ("/model [strong|cheap]", "help_model"),
    ("/glossary [add|remove <term>]", "help_glossary"),
    ("/tts on|off", "help_tts"),
    ("/remind [HH:MM|off]", "help_remind"),
    ("/gear [<stat> auto | <stat> <slot> <key> | now <stat>]", "help_gear"),
    ("/skills", "help_skills"),
]
