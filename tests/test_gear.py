"""Gear sets: picking them from owned gear, wearing them around the cron, /gear.

The fixture user is a level-120 wizard who owns 119 pieces; the expected
picks below were read off the content fixture by hand.
"""

from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.collector.snapshots import collect
from app.core import day_turn, gear
from app.core.harness import Harness
from app.core.replies import User
from app.core.turn import load_settings
from app.db.engine import session_scope
from app.habitica.client import HabiticaError
from app.habitica.types import user_stats_from_json
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task

USER = User("00000000-0000-4000-8000-000000000001")
ZURICH = ZoneInfo("Europe/Zurich")
AFTER_START = datetime(2026, 9, 12, 9, 1, tzinfo=UTC)

INT_SET = {
    "weapon": "weapon_special_nomadsScimitar",  # 16 + shield 16 beat the 18-INT staff
    "shield": "shield_special_wintryMirror",
    "head": "head_special_2",
    "armor": "armor_special_2",
    "eyewear": "eyewear_special_aetherMask",
}
CON_SET = {
    "weapon": "weapon_special_skeletonKey",
    "shield": "shield_special_goldenknight",
    "head": "head_special_dandyHat",
    "armor": "armor_special_2",
}
PER_SET = {
    "weapon": "weapon_special_2",
    "shield": "shield_special_goldenknight",
    "head": "head_special_pageHelm",
    "armor": "armor_special_snowSovereignRobes",
}


@pytest.fixture
def content() -> dict:
    return load_data("content")


@pytest.fixture
def owned() -> list[str]:
    return user_stats_from_json(load_data("user")).owned_gear


# ---- picking ----


def test_auto_picks_the_best_owned_item_per_slot(content: dict, owned: list[str]) -> None:
    assert gear.best_set("int", owned, content) == INT_SET
    assert gear.best_set("con", owned, content) == CON_SET
    assert gear.best_set("per", owned, content) == PER_SET
    assert gear.set_value("int", INT_SET, content) == 16 + 16 + 25 + 25 + 10


def test_a_two_handed_weapon_wins_only_when_it_beats_weapon_plus_shield(content: dict) -> None:
    staff = "weapon_wizard_6"  # 18 INT, two-handed
    assert gear.best_set("int", [staff, "weapon_special_nomadsScimitar"], content) == {
        "weapon": staff  # 18 > 16 alone
    }
    assert gear.best_set(
        "int", [staff, "weapon_special_nomadsScimitar", "shield_special_wintryMirror"], content
    ) == {"weapon": "weapon_special_nomadsScimitar", "shield": "shield_special_wintryMirror"}


def test_slots_without_the_stat_are_left_out(content: dict) -> None:
    assert gear.best_set("con", ["eyewear_special_aetherMask"], content) == {}  # 0 CON


# ---- wearing ----


async def test_equip_sends_only_what_is_not_on_weapon_before_shield() -> None:
    client = FakeHabiticaClient()
    equipped = {"weapon": "weapon_wizard_6", "head": "head_special_2", "armor": "armor_special_2"}

    keys = await gear.equip_set(client, equipped, INT_SET)

    assert keys == [
        "weapon_special_nomadsScimitar",
        "shield_special_wintryMirror",
        "eyewear_special_aetherMask",
    ]
    assert client.called("equip") == keys


def test_con_for_the_damage_int_for_the_mana() -> None:
    assert gear.before_cron(unfinished_dailies=True) == "con"
    assert gear.before_cron(unfinished_dailies=False) == "int"


def test_a_set_is_on_when_every_slot_matches() -> None:
    assert gear.is_on(CON_SET, {**CON_SET, "eyewear": "x"})
    assert not gear.is_on(CON_SET, {**CON_SET, "head": "head_special_2"})
    assert not gear.is_on({}, {})


async def turned_with(
    db_path: Path, client: FakeHabiticaClient, sets: dict[str, dict[str, str]]
) -> day_turn.DayTurned:
    client.user = user_stats_from_json({**load_data("user"), "needsCron": True})
    async with session_scope() as session:
        await collect(client, session, USER.id, AFTER_START)
        settings = await load_settings(session, USER.id)
        for stat, gear_set in sets.items():
            setattr(settings, f"gear_{stat}", gear_set)
        return await day_turn.turn_day(client, session, USER.id, AFTER_START, settings)


def equips_around_cron(client: FakeHabiticaClient) -> tuple[list[str], list[str]]:
    cron = client.calls.index(("run_cron", None))
    before = [key for name, key in client.calls[:cron] if name == "equip"]
    after = [key for name, key in client.calls[cron:] if name == "equip"]
    return before, after


async def test_the_day_turn_wears_con_for_an_unfinished_daily_then_per(db_path: Path) -> None:
    """The fixture user already wears the golden-knight shield, the PER weapon and the PER armor."""
    client = FakeHabiticaClient(tasks=[make_task("daily", "Gym", completed=False, isDue=True)])

    turned = await turned_with(db_path, client, {"con": CON_SET, "int": INT_SET, "per": PER_SET})

    before, after = equips_around_cron(client)
    assert before == ["weapon_special_skeletonKey", "head_special_dandyHat", "armor_special_2"]
    assert after == [
        "weapon_special_2",
        "head_special_pageHelm",
        "armor_special_snowSovereignRobes",
    ]
    assert (turned.worn_for_cron, turned.worn_now) == ("con", "per")
    assert [k.task.text for k in turned.due_today] == ["Gym"]


async def test_the_day_turn_wears_int_when_everything_is_done(db_path: Path) -> None:
    client = FakeHabiticaClient(tasks=[make_task("daily", "Gym", completed=True, isDue=True)])

    turned = await turned_with(db_path, client, {"con": CON_SET, "int": INT_SET, "per": PER_SET})

    before, _ = equips_around_cron(client)
    assert before == [
        "weapon_special_nomadsScimitar",
        "shield_special_wintryMirror",
        "head_special_2",
        "armor_special_2",
    ]  # the aether mask is on already
    assert turned.worn_for_cron == "int"


async def test_without_sets_nothing_is_equipped(db_path: Path) -> None:
    client = FakeHabiticaClient(tasks=[make_task("daily", "Gym", completed=False, isDue=True)])

    turned = await turned_with(db_path, client, {})

    assert client.called("equip") == []
    assert client.called("run_cron") == [None]
    assert (turned.worn_for_cron, turned.worn_now) == (None, None)


class RefusingEquips(FakeHabiticaClient):
    async def equip(self, key: str) -> None:
        self.calls.append(("equip", key))
        raise HabiticaError(404, "NotFound", "Item not found.")


async def test_a_refused_equip_does_not_keep_the_day_from_turning(db_path: Path) -> None:
    client = RefusingEquips(tasks=[make_task("daily", "Gym", completed=False, isDue=True)])

    turned = await turned_with(db_path, client, {"con": CON_SET, "int": INT_SET, "per": PER_SET})

    before, after = equips_around_cron(client)
    assert before and after  # both sets were tried
    assert client.called("run_cron") == [None]
    assert (turned.worn_for_cron, turned.worn_now) == (None, None)
    assert [k.task.text for k in turned.due_today] == ["Gym"]


# ---- /gear ----


async def test_gear_command_builds_shows_sets_and_equips(db_path: Path) -> None:
    client = FakeHabiticaClient()
    harness = Harness(habitica=client, tz=ZURICH)

    overview = (await harness.command_received(USER, "gear", "")).text
    assert "INT: not set — /gear int auto" in overview
    assert "PER: not set" in overview and "CON: not set" in overview

    built = (await harness.command_received(USER, "gear", "int auto")).text
    assert built.startswith("INT (+92): weapon: ")
    assert "shield: " in built and "eyewear: " in built
    async with session_scope() as session:
        assert (await load_settings(session, USER.id)).gear_int == INT_SET

    slot = await harness.command_received(USER, "gear", "int shield shield_special_diamondStave")
    assert "+92" in slot.text  # 16 for 16
    async with session_scope() as session:
        assert (await load_settings(session, USER.id)).gear_int["shield"] == (
            "shield_special_diamondStave"
        )

    worn = (await harness.command_received(USER, "gear", "now int")).text
    assert worn == (
        "INT set on: Nomad's Scimitar, Diamond Stave, Nameless Helm, Jean Chalard's Noble Tunic"
    )
    assert len(client.called("equip")) == 4  # the aether mask was on already
    # The set is on now; the endpoint toggles, so nothing may be sent twice.
    assert (await harness.command_received(USER, "gear", "now int")).text == (
        "The INT set is already on."
    )
    assert "INT (+92) — on now:" in (await harness.command_received(USER, "gear", "")).text


async def test_gear_command_refuses_bad_input(db_path: Path) -> None:
    harness = Harness(habitica=FakeHabiticaClient(), tz=ZURICH)

    assert (await harness.command_received(USER, "gear", "str auto")).text.startswith("Usage:")
    assert (await harness.command_received(USER, "gear", "int")).text.startswith("Usage:")
    assert (await harness.command_received(USER, "gear", "int hat head_special_2")).text.startswith(
        "Unknown slot hat"
    )
    assert (await harness.command_received(USER, "gear", "int head head_nope")).text.startswith(
        "No item with key head_nope"
    )
    assert (
        await harness.command_received(USER, "gear", "int shield shield_base_0")
    ).text.startswith("You don't own")
    assert (await harness.command_received(USER, "gear", "now con")).text.startswith("CON: not set")
