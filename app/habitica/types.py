"""What Habitica's API returns, as dataclasses.

Only the fields the app reasons about are parsed. Every dataclass keeps the
JSON it came from in `raw`, so a field we did not parse is never lost — the
collector stores `raw` next to the parsed columns.

Shapes were checked against real responses (see `tests/fixtures/habitica/`):

- `preferences.timezoneOffset` follows the JavaScript convention: minutes
  *behind* UTC, so Zurich in summer is `-120`.
- A daily's `history[]` has one entry per scoring event, not per day; several
  entries can share a Habitica day. Habit entries carry `scoredUp` /
  `scoredDown` instead of `isDue` / `completed`.
- Timestamps come as ISO strings with a `Z`, except history dates, which are
  epoch milliseconds.
"""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Literal

TaskType = Literal["habit", "daily", "todo", "reward"]

GEAR_SLOTS = ("weapon", "armor", "head", "shield", "back", "headAccessory", "eyewear", "body")
GEAR_STATS = ("str", "int", "per", "con")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_iso_required(value: str) -> datetime:
    parsed = parse_iso(value)
    if parsed is None:
        raise ValueError("expected an ISO timestamp, got an empty value")
    return parsed


def parse_epoch_ms(value: int | float) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


@dataclass(frozen=True)
class UserStats:
    hp: float
    mp: float
    exp: float
    gp: float
    lvl: int
    class_name: str
    to_next_level: int
    max_hp: int
    max_mp: int
    day_start: int
    timezone_offset: int
    last_cron: datetime | None
    needs_cron: bool
    equipped: dict[str, str]
    owned_gear: list[str]
    raw: dict[str, Any] = field(repr=False)


def user_stats_from_json(data: dict[str, Any]) -> UserStats:
    """`data` is the object under `"data"` in a `GET /user` response."""
    stats = data["stats"]
    preferences = data["preferences"]
    gear = data.get("items", {}).get("gear", {})
    return UserStats(
        hp=float(stats["hp"]),
        mp=float(stats["mp"]),
        exp=float(stats["exp"]),
        gp=float(stats["gp"]),
        lvl=int(stats["lvl"]),
        class_name=stats["class"],
        to_next_level=int(stats["toNextLevel"]),
        max_hp=int(stats["maxHealth"]),
        max_mp=int(stats["maxMP"]),
        day_start=int(preferences["dayStart"]),
        timezone_offset=int(preferences["timezoneOffset"]),
        last_cron=parse_iso(data.get("lastCron")),
        needs_cron=bool(data.get("needsCron", False)),
        equipped=dict(gear.get("equipped", {})),
        owned_gear=[key for key, owned in gear.get("owned", {}).items() if owned],
        raw=data,
    )


@dataclass(frozen=True)
class HistoryEntry:
    at: datetime
    value: float
    # dailies
    is_due: bool | None = None
    completed: bool | None = None
    # habits
    scored_up: int | None = None
    scored_down: int | None = None


def history_entry_from_json(data: dict[str, Any]) -> HistoryEntry:
    return HistoryEntry(
        at=parse_epoch_ms(data["date"]),
        value=float(data.get("value", 0.0)),
        is_due=data.get("isDue"),
        completed=data.get("completed"),
        scored_up=data.get("scoredUp"),
        scored_down=data.get("scoredDown"),
    )


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    text: str
    completed: bool


def checklist_item_from_json(data: dict[str, Any]) -> ChecklistItem:
    return ChecklistItem(id=data["id"], text=data["text"], completed=bool(data["completed"]))


@dataclass(frozen=True)
class Task:
    id: str
    type: TaskType
    text: str
    notes: str
    value: float
    priority: float
    created_at: datetime
    updated_at: datetime
    tags: list[str]
    checklist: list[ChecklistItem]
    history: list[HistoryEntry]
    # dailies and todos
    completed: bool | None = None
    # dailies
    is_due: bool | None = None
    streak: int | None = None
    frequency: str | None = None
    repeat: dict[str, bool] | None = None
    every_x: int | None = None
    start_date: datetime | None = None
    # habits
    up: bool | None = None
    down: bool | None = None
    counter_up: int | None = None
    counter_down: int | None = None
    # todos
    due_date: datetime | None = None
    date_completed: datetime | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def checklist_done(self) -> int:
        return sum(1 for item in self.checklist if item.completed)


def task_from_json(data: dict[str, Any]) -> Task:
    """`data` is one element of `GET /tasks/user`'s `"data"` list."""
    task_type: TaskType = data["type"]
    task = Task(
        id=data["id"],
        type=task_type,
        text=data["text"],
        notes=data.get("notes") or "",
        value=float(data.get("value", 0.0)),
        priority=float(data.get("priority", 1.0)),
        created_at=parse_iso_required(data["createdAt"]),
        updated_at=parse_iso_required(data["updatedAt"]),
        tags=list(data.get("tags", [])),
        checklist=[checklist_item_from_json(item) for item in data.get("checklist", [])],
        history=[history_entry_from_json(entry) for entry in data.get("history", [])],
        completed=data.get("completed") if task_type in ("daily", "todo") else None,
        raw=data,
    )
    if task_type == "daily":
        return _with_daily_fields(task, data)
    if task_type == "habit":
        return _with_habit_fields(task, data)
    if task_type == "todo":
        return _with_todo_fields(task, data)
    return task


def _with_daily_fields(task: Task, data: dict[str, Any]) -> Task:
    return replace(
        task,
        is_due=data.get("isDue"),
        streak=int(data.get("streak", 0)),
        frequency=data.get("frequency"),
        repeat=dict(data.get("repeat", {})),
        every_x=data.get("everyX"),
        start_date=parse_iso(data.get("startDate")),
    )


def _with_habit_fields(task: Task, data: dict[str, Any]) -> Task:
    return replace(
        task,
        up=data.get("up"),
        down=data.get("down"),
        counter_up=int(data.get("counterUp", 0)),
        counter_down=int(data.get("counterDown", 0)),
    )


def _with_todo_fields(task: Task, data: dict[str, Any]) -> Task:
    return replace(
        task,
        due_date=parse_iso(data.get("date")),
        date_completed=parse_iso(data.get("dateCompleted")),
    )


@dataclass(frozen=True)
class GearItem:
    key: str
    text: str
    slot: str
    klass: str
    stats: dict[str, int]  # keyed by GEAR_STATS
    value: float
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def gear_item_from_json(data: dict[str, Any]) -> GearItem:
    """`data` is one value of `content.gear.flat`."""
    return GearItem(
        key=data["key"],
        text=data.get("text", data["key"]),
        slot=data["type"],
        klass=data.get("klass", ""),
        stats={name: int(data.get(name, 0)) for name in GEAR_STATS},
        value=float(data.get("value", 0)),
        raw=data,
    )


def gear_from_content(content: dict[str, Any]) -> dict[str, GearItem]:
    """`content` is the object under `"data"` in a `GET /content` response."""
    flat = content["gear"]["flat"]
    return {key: gear_item_from_json(item) for key, item in flat.items()}


@dataclass(frozen=True)
class ScoreResult:
    """What `POST /tasks/{id}/score/{direction}` returns: the stats after scoring."""

    delta: float
    hp: float
    mp: float
    exp: float
    gp: float
    lvl: int
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def score_result_from_json(data: dict[str, Any]) -> ScoreResult:
    return ScoreResult(
        delta=float(data.get("delta", 0.0)),
        hp=float(data["hp"]),
        mp=float(data["mp"]),
        exp=float(data["exp"]),
        gp=float(data["gp"]),
        lvl=int(data["lvl"]),
        raw=data,
    )


@dataclass(frozen=True)
class Webhook:
    id: str
    url: str
    type: str
    enabled: bool
    options: dict[str, bool]
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def webhook_from_json(data: dict[str, Any]) -> Webhook:
    return Webhook(
        id=data["id"],
        url=data["url"],
        type=data["type"],
        enabled=bool(data.get("enabled", True)),
        options=dict(data.get("options", {})),
        raw=data,
    )
