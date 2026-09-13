"""SQLAlchemy models. One file until it hurts.

Conventions:

- every table carries `user_id` (the Habitica user id) even though one
  deployment serves one person;
- every table that stores an API response keeps it whole, so a field we did
  not parse is never lost. `user_snapshots` keeps its `raw` inline; the tables
  that repeat the same JSON many times (`task_snapshots`, `webhook_deliveries`)
  point at `raw_blobs`, where each distinct document is stored once under its
  hash — the way git stores blobs;
- timestamps are stored in UTC and come back timezone-aware (`UtcDateTime`);
  SQLite itself has no notion of a timezone.
"""

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


class UtcDateTime(TypeDecorator[datetime]):
    """Refuses naive datetimes on the way in; returns aware UTC on the way out."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime; pass an aware one")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class RawBlob(Base):
    """One distinct JSON document, keyed by the sha256 of its canonical form."""

    __tablename__ = "raw_blobs"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    json: Mapped[dict[str, Any]] = mapped_column(JSON)


class UserSnapshot(Base):
    __tablename__ = "user_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    taken_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    hp: Mapped[float] = mapped_column(Float)
    mp: Mapped[float] = mapped_column(Float)
    exp: Mapped[float] = mapped_column(Float)
    gp: Mapped[float] = mapped_column(Float)
    lvl: Mapped[int] = mapped_column(Integer)
    class_name: Mapped[str] = mapped_column("class", String(16))
    to_next_level: Mapped[int] = mapped_column(Integer)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)


class TaskRow(Base):
    """Every task we have ever seen; `deleted_at` marks the ones that went away."""

    __tablename__ = "tasks"
    __table_args__ = (UniqueConstraint("user_id", "habitica_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    habitica_id: Mapped[str] = mapped_column(String(36))
    type: Mapped[str] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    first_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON)


class TaskSnapshot(Base):
    __tablename__ = "task_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    taken_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    completed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_due: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    streak: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value: Mapped[float] = mapped_column(Float)
    counter_up: Mapped[int | None] = mapped_column(Integer, nullable=True)
    counter_down: Mapped[int | None] = mapped_column(Integer, nullable=True)
    checklist_done: Mapped[int] = mapped_column(Integer, default=0)
    checklist_total: Mapped[int] = mapped_column(Integer, default=0)
    raw_hash: Mapped[str] = mapped_column(ForeignKey("raw_blobs.hash"))
    blob: Mapped[RawBlob] = relationship(lazy="selectin")

    @property
    def raw(self) -> dict[str, Any]:
        return self.blob.json


class TaskEvent(Base):
    """Something happened to a task.

    kind: scored | created | updated | deleted | completed | uncompleted | checklist | started
    source: bot | snapshot_diff | webhook | backfill | calendar | reminder
    """

    __tablename__ = "task_events"
    __table_args__ = (Index("ix_task_events_task_id_at", "task_id", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    direction: Mapped[str | None] = mapped_column(String(4), nullable=True)
    delta: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(16))
    raw: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class DailyRecord(Base):
    """One row per daily per Habitica day. What the analytics read."""

    __tablename__ = "daily_records"
    __table_args__ = (UniqueConstraint("task_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    day: Mapped[date] = mapped_column(Date, index=True)
    was_due: Mapped[bool] = mapped_column(Boolean)
    completed: Mapped[bool] = mapped_column(Boolean)
    source: Mapped[str] = mapped_column(String(16))


class WebhookDelivery(Base):
    """Everything Habitica sends us, untouched."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    received_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    type: Mapped[str] = mapped_column(String(32))
    raw_hash: Mapped[str] = mapped_column(ForeignKey("raw_blobs.hash"))
    blob: Mapped[RawBlob] = relationship(lazy="selectin")

    @property
    def raw(self) -> dict[str, Any]:
        return self.blob.json


class UserSettings(Base):
    __tablename__ = "user_settings"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    language: Mapped[str] = mapped_column(String(2), default="en")
    model: Mapped[str] = mapped_column(String(8), default="strong")
    tts: Mapped[str] = mapped_column(String(3), default="off")
    reminder_time: Mapped[str | None] = mapped_column(String(5), default="21:00", nullable=True)
    gear_int: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    gear_per: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    gear_con: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    last_reminder_day: Mapped[date | None] = mapped_column(Date, nullable=True)
    dismissed_proposals: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    recent_suggestions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)


class ConversationState(Base):
    """What the bot is waiting for from this user, if anything."""

    __tablename__ = "conversation_state"

    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class Message(Base):
    """The chat log, both directions.

    direction: in | out
    kind: text | command | button | voice
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    direction: Mapped[str] = mapped_column(String(3))
    kind: Mapped[str] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text)


class LlmCall(Base):
    """One request to the model provider, successful or not."""

    __tablename__ = "llm_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class TelegramButton(Base):
    """One rendered inline button. Telegram callback data is capped at 64 bytes,
    so the callback carries this row's id and the action and payload live here."""

    __tablename__ = "telegram_buttons"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
