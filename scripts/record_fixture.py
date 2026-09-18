"""Record scrubbed Habitica responses as test fixtures.

    uv run python scripts/record_fixture.py

Reads credentials from .env, hits the real API once, and writes
`tests/fixtures/habitica/{user,tasks,completed_todos,content}.json`.

Scrubbing: the user object is reduced to a whitelist of top-level keys (no
profile, auth, inbox, party, ...); every id is replaced by a deterministic
fake; task notes are blanked because they are personal; `/content` is cut
down to the gear the account owns or wears. Task texts are kept — the
fixtures should look like a real account.

Mind the Habitica day: any call after `dayStart` triggers cron, exactly like
opening the app would.
"""

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from app.habitica.client import client_from_settings

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "habitica"
FAKE_USER_ID = "00000000-0000-4000-8000-000000000001"
FAKE_NAMESPACE = uuid.UUID("2b1c6a0e-1c6e-4c3a-9d0e-3f7c5b7a1e11")

USER_KEYS = (
    "_id",
    "id",
    "stats",
    "preferences",
    "items",
    "lastCron",
    "needsCron",
    "history",
    "tasksOrder",
    "flags",
)


def fake_id(real: str) -> str:
    return str(uuid.uuid5(FAKE_NAMESPACE, real))


def scrub_user(data: dict[str, Any]) -> dict[str, Any]:
    kept = {key: data[key] for key in USER_KEYS if key in data}
    kept["_id"] = kept["id"] = FAKE_USER_ID
    # items holds pets, mounts, quests... gear is all we parse.
    kept["items"] = {"gear": data["items"]["gear"]}
    kept["tasksOrder"] = {
        kind: [fake_id(task_id) for task_id in ids] for kind, ids in kept["tasksOrder"].items()
    }
    return kept


def scrub_task(data: dict[str, Any]) -> dict[str, Any]:
    task = dict(data)
    task["_id"] = task["id"] = fake_id(task["id"])
    task["userId"] = FAKE_USER_ID
    task["notes"] = ""
    task["tags"] = [fake_id(tag) for tag in task.get("tags", [])]
    task["checklist"] = [{**item, "id": fake_id(item["id"])} for item in task.get("checklist", [])]
    if task.get("challenge"):
        task["challenge"] = {
            key: fake_id(value) if isinstance(value, str) else value
            for key, value in task["challenge"].items()
        }
    return task


SPELL_CLASSES = ("warrior", "wizard", "rogue", "healer")  # the API calls the mage "wizard"


def trim_content(content: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
    gear = user["items"]["gear"]
    wanted = set(gear.get("equipped", {}).values()) | set(gear.get("costume", {}).values())
    wanted |= {key for key, owned in gear.get("owned", {}).items() if owned}
    flat = content["gear"]["flat"]
    return {
        "gear": {"flat": {key: flat[key] for key in sorted(wanted) if key in flat}},
        # The class skills; `special` holds seasonal items the bot does not cast.
        "spells": {cls: content["spells"][cls] for cls in SPELL_CLASSES},
    }


def envelope(data: Any) -> dict[str, Any]:
    return {"success": True, "data": data}


def write(name: str, payload: dict[str, Any]) -> None:
    path = FIXTURE_DIR / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    print(f"wrote {path} ({path.stat().st_size} bytes)")


async def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    async with client_from_settings() as client:
        user = (await client.get_user()).raw
        tasks = [task.raw for task in await client.get_tasks()]
        completed = [task.raw for task in await client.get_completed_todos()]
        content = await client.get_content(force_refresh=True)

    write("user", envelope(scrub_user(user)))
    write("tasks", envelope([scrub_task(task) for task in tasks]))
    write("completed_todos", envelope([scrub_task(task) for task in completed]))
    write("content", envelope(trim_content(content, user)))


if __name__ == "__main__":
    asyncio.run(main())
