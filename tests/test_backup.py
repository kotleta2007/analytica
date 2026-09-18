"""scripts/backup.py: a checked copy per day, the last 30 kept."""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from scripts import backup


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "analytica.db"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE t (n INTEGER)")
    connection.executemany("INSERT INTO t VALUES (?)", [(1,), (2,), (3,)])
    connection.commit()
    # Kept open, and dirty: the app is writing while the backup runs.
    connection.execute("INSERT INTO t VALUES (4)")
    return path


def rows(path: Path) -> list[int]:
    connection = sqlite3.connect(path)
    try:
        return [n for (n,) in connection.execute("SELECT n FROM t ORDER BY n")]
    finally:
        connection.close()


def test_copy_is_named_by_day_and_holds_the_committed_rows(db: Path, tmp_path: Path) -> None:
    out = backup.backup(db, tmp_path / "backups", today=date(2026, 9, 16))

    assert out == tmp_path / "backups" / "analytica-2026-09-16.db"
    assert rows(out) == [1, 2, 3]


def test_same_day_rerun_overwrites_that_days_copy(db: Path, tmp_path: Path) -> None:
    first = backup.backup(db, tmp_path / "backups", today=date(2026, 9, 16))
    second = backup.backup(db, tmp_path / "backups", today=date(2026, 9, 16))

    assert first == second
    assert list((tmp_path / "backups").iterdir()) == [first]


def test_only_the_newest_copies_are_kept(db: Path, tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    directory.mkdir()
    for day in range(1, 6):
        (directory / f"analytica-2026-09-0{day}.db").write_bytes(b"")
    (directory / "unrelated.db").write_bytes(b"")

    backup.backup(db, directory, keep=3, today=date(2026, 9, 16))

    assert sorted(p.name for p in directory.iterdir()) == [
        "analytica-2026-09-04.db",
        "analytica-2026-09-05.db",
        "analytica-2026-09-16.db",
        "unrelated.db",
    ]


def test_an_unreadable_copy_is_an_error(tmp_path: Path) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)

    with pytest.raises(sqlite3.DatabaseError, match="not a database"):
        backup.verify(broken)


def test_database_path_comes_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/analytica.db")
    backup.get_settings.cache_clear()
    try:
        assert backup.database_path() == Path("./data/analytica.db")
    finally:
        backup.get_settings.cache_clear()
