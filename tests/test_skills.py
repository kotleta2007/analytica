"""Class skills: what the user can cast, the cast_skill tool, /skills, the prompt.

The content fixture carries the four classes' skills; the user fixture is a
level-120 wizard, re-levelled here to 12 with 30 MP — Burst of Flames (11,
10 MP) and Ethereal Surge (12, 30 MP) unlocked, Earthquake and Chilling
Frost not yet.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.collector.snapshots import collect
from app.core.harness import Harness
from app.core.replies import User
from app.core.skills import available_skills, find_skill, skill_line
from app.core.turn import Turn, load_settings
from app.db.engine import session_scope
from app.habitica.types import skills_from_content, user_stats_from_json
from app.llm import tools
from app.llm.client import ToolCall
from app.llm.prompts import build_system_prompt
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data
from tests.task_factory import make_task

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 13, 10, 0, tzinfo=UTC)
TZ = ZoneInfo("Europe/Zurich")
ESSAY = make_task("todo", "Essay draft", task_id="e55a7000-0000-4000-8000-000000000001")


def mage(lvl: int = 12, mp: float = 30, class_name: str = "wizard") -> dict[str, Any]:
    raw = load_data("user")
    raw["stats"] = {**raw["stats"], "lvl": lvl, "mp": mp, "class": class_name}
    return raw


@pytest.fixture
def content() -> dict[str, Any]:
    return load_data("content")


# ---- the table and what is available ----


def test_the_content_carries_four_skills_per_class(content: dict[str, Any]) -> None:
    skills = skills_from_content(content)

    assert len(skills) == 16
    fireball = next(s for s in skills if s.key == "fireball")
    assert (fireball.text, fireball.class_name, fireball.mana, fireball.level, fireball.target) == (
        "Burst of Flames",
        "wizard",
        10,
        11,
        "task",
    )


def test_a_level_12_mage_sees_two_skills_with_castable_following_the_mana(
    content: dict[str, Any],
) -> None:
    available = available_skills(user_stats_from_json(mage(lvl=12, mp=30)), content)
    assert [(a.skill.text, a.castable) for a in available] == [
        ("Burst of Flames", True),
        ("Ethereal Surge", True),
    ]

    short = available_skills(user_stats_from_json(mage(lvl=12, mp=20)), content)
    assert [(a.skill.text, a.castable) for a in short] == [
        ("Burst of Flames", True),
        ("Ethereal Surge", False),
    ]
    assert available_skills(user_stats_from_json(mage(lvl=10)), content) == []
    warrior = available_skills(user_stats_from_json(mage(lvl=14, class_name="warrior")), content)
    assert [a.skill.key for a in warrior] == [
        "smash",
        "intimidate",
        "valorousPresence",
        "defensiveStance",
    ]


def test_find_by_key_or_name_ignoring_case(content: dict[str, Any]) -> None:
    available = available_skills(user_stats_from_json(mage()), content)

    assert find_skill(available, "fireball") is available[0]
    assert find_skill(available, "burst of flames") is available[0]
    assert find_skill(available, "Ethereal Surge") is available[1]
    assert find_skill(available, "earth") is None  # not unlocked at 12


def test_the_prompt_line_carries_cost_target_and_effect(content: dict[str, Any]) -> None:
    available = available_skills(user_stats_from_json(mage()), content)

    assert skill_line(available[0]).startswith(
        "Burst of Flames (fireball) — 10 MP, on a task: You summon XP"
    )


def test_the_system_prompt_lists_unlocked_skills_with_the_mana_compared(
    content: dict[str, Any],
) -> None:
    stats = user_stats_from_json(mage(lvl=12, mp=20))
    prompt = build_system_prompt(
        language="en",
        now=NOW,
        tz=TZ,
        stats=stats,
        tasks=[],
        recent_summaries=[],
        skills=available_skills(stats, content),
    )

    assert "Class skills unlocked (cast_skill):" in prompt
    assert "- Burst of Flames (fireball) — 10 MP, on a task: " in prompt
    assert "[castable now]" in prompt and "Ethereal Surge (mpheal) — 30 MP, on the party" in prompt
    assert "[not enough mana]" in prompt
    assert "Skills: each class unlocks four between levels 11 and 14." in prompt


# ---- the tool ----


@pytest.fixture
async def seeded(db_path: Path) -> FakeHabiticaClient:
    client = FakeHabiticaClient(user=user_stats_from_json(mage()), tasks=[ESSAY])
    async with session_scope() as session:
        await collect(client, session, USER.id, NOW)
    client.calls.clear()
    return client


class TurnScope:
    def __init__(self, client: FakeHabiticaClient) -> None:
        self.client = client

    async def __aenter__(self) -> Turn:
        self.scope = session_scope()
        session = await self.scope.__aenter__()
        settings = await load_settings(session, USER.id)
        return Turn(session, USER, settings, self.client, NOW, TZ)  # type: ignore[arg-type]

    async def __aexit__(self, *exc_info: object) -> None:
        await self.scope.__aexit__(*exc_info)


def call(**arguments: object) -> ToolCall:
    return ToolCall(id="call_1", name="cast_skill", arguments=dict(arguments))


async def test_a_task_skill_without_a_task_is_an_error(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call(skill="fireball"))

    assert result.content == {"error": "Burst of Flames acts on a task; pass task_id"}
    assert seeded.called("cast_skill") == []


async def test_a_self_or_party_skill_with_a_task_is_an_error(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        result = await tools.dispatch(turn, call(skill="Ethereal Surge", task_id="e55a7000"))

    assert result.content["error"] == "Ethereal Surge does not take a task; call it without task_id"


async def test_a_skill_of_another_class_or_level_is_an_error(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        other = await tools.dispatch(turn, call(skill="Brutal Smash", task_id="e55a7000"))
        locked = await tools.dispatch(turn, call(skill="Chilling Frost"))

    assert other.content["error"].startswith("no skill 'Brutal Smash' for a level 12 mage")
    assert locked.content["error"].startswith("no skill 'Chilling Frost' for a level 12 mage")


async def test_not_enough_mana_is_an_error_not_a_cast(db_path: Path) -> None:
    client = FakeHabiticaClient(user=user_stats_from_json(mage(mp=4)), tasks=[ESSAY])
    async with session_scope() as session:
        await collect(client, session, USER.id, NOW)
    async with TurnScope(client) as turn:
        result = await tools.dispatch(turn, call(skill="fireball", task_id="e55a7000"))

    assert result.content == {"error": "Burst of Flames costs 10 MP and the user has 4"}
    assert client.called("cast_skill") == []


async def test_a_cast_asks_for_confirmation_then_casts_on_the_task(
    seeded: FakeHabiticaClient,
) -> None:
    async with TurnScope(seeded) as turn:
        offer = await tools.dispatch(turn, call(skill="burst of flames", task_id="e55a7000"))
        pending = await tools.pending_action(turn)
        assert pending is not None
        assert seeded.called("cast_skill") == []
        assert offer.content["status"] == "needs_confirmation"
        assert offer.content["action"] == "cast Burst of Flames on “Essay draft” (10 MP)"
        assert [b.action for b in offer.buttons] == ["confirm", "cancel"]
        assert tools.describe_pending(pending, "fr") == (
            "lancer Burst of Flames sur « Essay draft » (10 PM)"
        )

        done = await tools.run_pending(turn, pending)

    assert seeded.called("cast_skill") == [("fireball", ESSAY.id)]
    assert done.content == {"cast": "Burst of Flames", "mana_spent": 10, "target": "Essay draft"}
    assert done.changed_tasks is True  # the harness collects after: stats and the task changed


async def test_a_self_skill_is_cast_without_a_target(seeded: FakeHabiticaClient) -> None:
    async with TurnScope(seeded) as turn:
        offer = await tools.dispatch(turn, call(skill="mpheal"))
        pending = await tools.pending_action(turn)
        assert pending is not None
        assert offer.content["action"] == "cast Ethereal Surge (30 MP)"
        await tools.run_pending(turn, pending)

    assert seeded.called("cast_skill") == [("mpheal", None)]


# ---- /skills ----


async def test_skills_command_lists_what_can_be_cast_now(db_path: Path) -> None:
    client = FakeHabiticaClient(user=user_stats_from_json(mage(lvl=12, mp=20)))
    harness = Harness(habitica=client, tz=TZ)

    reply = await harness.command_received(USER, "skills", "")

    assert reply.text == (
        "mage, level 12, 20/438 MP:\n"
        "✅ Burst of Flames — 10 MP, on a task\n"
        "⏳ Ethereal Surge — 30 MP, on the party"
    )


async def test_skills_command_before_level_11(db_path: Path) -> None:
    client = FakeHabiticaClient(user=user_stats_from_json(mage(lvl=9, mp=20)))
    harness = Harness(habitica=client, tz=TZ)

    reply = await harness.command_received(USER, "skills", "")

    assert reply.text.endswith("No skills yet — the first one unlocks at level 11.")
