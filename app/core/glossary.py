"""The words the recogniser gets wrong for this user, and the context that corrects them.

`asr_glossary` holds terms the user added (`/glossary add`, the model's
`add_glossary_term`, or a ➕ button after an amended transcript). The bias
context puts them next to the task names, the Habitica vocabulary and the
command names, so the cleanup pass knows which spellings are right.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tasks import load_known_tasks, open_tasks
from app.db.models import AsrGlossaryTerm

MAX_TERM_LENGTH = 64
FROM_COMMAND = "command"
FROM_TOOL = "tool"
FROM_AMENDMENT = "amendment"
HABITICA_WORDS = (
    "Habitica",
    "habit",
    "daily",
    "todo",
    "checklist",
    "streak",
    "HP",
    "MP",
    "mana",
    "XP",
    "gold",
    "level",
    "cron",
    "warrior",
    "mage",
    "rogue",
    "healer",
)


class GlossaryTermError(ValueError):
    """The term is empty, too long, or spans several lines."""


def normalise(term: str) -> str:
    cleaned = " ".join(term.split())
    if not cleaned:
        raise GlossaryTermError("a term cannot be empty")
    if len(cleaned) > MAX_TERM_LENGTH:
        raise GlossaryTermError(f"a term is at most {MAX_TERM_LENGTH} characters")
    return cleaned


async def list_terms(session: AsyncSession, user_id: str) -> list[str]:
    rows = await session.scalars(
        select(AsrGlossaryTerm.term)
        .where(AsrGlossaryTerm.user_id == user_id)
        .order_by(func.lower(AsrGlossaryTerm.term))
    )
    return list(rows)


async def find_term(session: AsyncSession, user_id: str, term: str) -> AsrGlossaryTerm | None:
    return await session.scalar(
        select(AsrGlossaryTerm).where(
            AsrGlossaryTerm.user_id == user_id,
            func.lower(AsrGlossaryTerm.term) == normalise(term).lower(),
        )
    )


async def add_term(
    session: AsyncSession, user_id: str, term: str, added_from: str, now: datetime
) -> bool:
    """Stores the term; False when it was already there (case-insensitively)."""
    cleaned = normalise(term)
    if await find_term(session, user_id, cleaned) is not None:
        return False
    session.add(
        AsrGlossaryTerm(user_id=user_id, term=cleaned, added_from=added_from, created_at=now)
    )
    await session.flush()
    return True


async def remove_term(session: AsyncSession, user_id: str, term: str) -> bool:
    row = await find_term(session, user_id, term)
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


async def bias_context(session: AsyncSession, user_id: str, command_names: list[str]) -> str:
    """What a transcript is likely to contain, for the cleanup pass: glossary first."""
    terms = await list_terms(session, user_id)
    tasks = [known.task.text for known in open_tasks(await load_known_tasks(session, user_id))]
    lines = []
    if terms:
        lines.append("User's glossary (these spellings are right): " + ", ".join(terms))
    if tasks:
        lines.append("Task names: " + "; ".join(tasks))
    lines.append("Habitica words: " + ", ".join(HABITICA_WORDS))
    lines.append("Bot commands: " + ", ".join(f"/{name}" for name in command_names))
    return "\n".join(lines)
