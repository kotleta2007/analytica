"""Three gear sets — INT, PER, CON — and which one to wear when.

Habitica applies the stats you wear at the moment things happen: CON when
cron deals the damage for missed dailies, INT when cron regenerates mana
(more max MP, more regen), PER while you score tasks during the day (gold and
drops). So the day turn wears CON or INT for the cron and PER after it.

A set is `{slot: item_key}` in `user_settings.gear_int/per/con`, built by
`/gear <stat> auto` from the gear the user owns and the stats in `/content`,
or slot by slot. `POST /user/equip/equipped/{key}` *toggles*, so an item that
is already on is never sent.
"""

from typing import Any

from app.db.models import UserSettings
from app.habitica.client import HabiticaClient

STATS = ("int", "per", "con")
# Habitica's slots. Weapon before shield: a one-hander + shield then replaces a staff cleanly
# (equipping a two-handed weapon drops the shield, and a shield drops a two-hander).
SLOTS = ("weapon", "shield", "head", "armor", "headAccessory", "eyewear", "back", "body")


def gear_table(content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return content["gear"]["flat"]


def best_set(stat: str, owned: list[str], content: dict[str, Any]) -> dict[str, str]:
    """The highest-`stat` owned item per slot; a two-handed weapon only if it beats
    the best one-hander plus the best shield together. Slots where nothing owned
    carries the stat are left out."""
    flat = gear_table(content)
    items = [flat[key] for key in owned if key in flat]
    best: dict[str, tuple[int, str]] = {}
    two_handed: tuple[int, str] = (0, "")
    for item in items:
        value = int(item.get(stat, 0))
        if value <= 0:
            continue
        if item.get("twoHanded"):
            two_handed = max(two_handed, (value, item["key"]))
            continue
        slot = item["type"]
        best[slot] = max(best.get(slot, (0, "")), (value, item["key"]))
    pair = best.get("weapon", (0, ""))[0] + best.get("shield", (0, ""))[0]
    if two_handed[0] > pair:
        best["weapon"] = two_handed
        best.pop("shield", None)
    return {slot: key for slot, (_, key) in best.items()}


def set_value(stat: str, gear_set: dict[str, str], content: dict[str, Any]) -> int:
    flat = gear_table(content)
    return sum(int(flat.get(key, {}).get(stat, 0)) for key in gear_set.values())


def item_name(key: str, content: dict[str, Any]) -> str:
    return gear_table(content).get(key, {}).get("text", key)


def set_for(settings: UserSettings, stat: str) -> dict[str, str]:
    return dict(getattr(settings, f"gear_{stat}"))


def is_on(gear_set: dict[str, str], equipped: dict[str, str]) -> bool:
    return bool(gear_set) and all(equipped.get(slot) == key for slot, key in gear_set.items())


def before_cron(unfinished_dailies: bool) -> str:
    """CON takes the damage for what is still undone; INT means more mana from the regen."""
    return "con" if unfinished_dailies else "int"


async def equip_set(
    client: HabiticaClient, equipped: dict[str, str], gear_set: dict[str, str]
) -> list[str]:
    """Put on what is not on yet, in slot order; returns the keys equipped."""
    done: list[str] = []
    for slot in SLOTS:
        key = gear_set.get(slot)
        if key and equipped.get(slot) != key:
            await client.equip(key)
            done.append(key)
    return done


async def wear(
    client: HabiticaClient, equipped: dict[str, str], settings: UserSettings, stat: str
) -> str | None:
    """Wear the user's set for `stat`; None when no set is configured for it."""
    gear_set = set_for(settings, stat)
    if not gear_set:
        return None
    await equip_set(client, equipped, gear_set)
    return stat
