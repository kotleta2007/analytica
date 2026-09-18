"""Copy the database into data/backups/, check the copy, keep the last 30.

    uv run python scripts/backup.py

On the VPS `deploy/backup.sh` runs this inside the container every night, so
the host needs nothing but Docker. SQLite's online backup API is safe while
the app is writing; a plain copy of a WAL-mode database is not. A second run
on the same day overwrites that day's copy.
"""

import sqlite3
from datetime import date
from pathlib import Path

from sqlalchemy import make_url

from app.config import get_settings

KEEP = 30


def backup(db: Path, directory: Path, keep: int = KEEP, today: date | None = None) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    copy = directory / f"analytica-{(today or date.today()).isoformat()}.db"
    source = sqlite3.connect(db)
    target = sqlite3.connect(copy)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    verify(copy)
    prune(directory, keep)
    return copy


def verify(copy: Path) -> None:
    connection = sqlite3.connect(copy)
    try:
        (result,) = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if result != "ok":
        raise RuntimeError(f"backup {copy} failed quick_check: {result}")


def prune(directory: Path, keep: int) -> list[Path]:
    """Delete everything past the newest `keep` copies; the date in the name orders them."""
    copies = sorted(directory.glob("analytica-*.db"), reverse=True)
    for old in copies[keep:]:
        old.unlink()
    return copies[keep:]


def database_path() -> Path:
    database = make_url(get_settings().database_url).database
    if not database or database == ":memory:":
        raise SystemExit(f"nothing to back up: DATABASE_URL is {get_settings().database_url}")
    return Path(database)


if __name__ == "__main__":
    db = database_path()
    print(backup(db, db.parent / "backups"))
