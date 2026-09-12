"""Content-addressed storage for raw API documents.

`store_blobs` hashes each document's canonical JSON and inserts only the
hashes the table does not have yet. A task's JSON that did not change between
two snapshots costs one row of pointer, not another copy.
"""

import hashlib
import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RawBlob


def content_hash(document: dict[str, Any]) -> str:
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def store_blobs(session: AsyncSession, documents: Sequence[dict[str, Any]]) -> list[str]:
    """Make sure every document is stored; return their hashes, in order."""
    hashes = [content_hash(document) for document in documents]
    wanted = dict(zip(hashes, documents, strict=True))
    if not wanted:
        return hashes
    existing = set(await session.scalars(select(RawBlob.hash).where(RawBlob.hash.in_(wanted))))
    session.add_all(
        RawBlob(hash=digest, json=document)
        for digest, document in wanted.items()
        if digest not in existing
    )
    await session.flush()
    return hashes


async def store_blob(session: AsyncSession, document: dict[str, Any]) -> str:
    return (await store_blobs(session, [document]))[0]
