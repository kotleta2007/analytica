"""The class skills the user can cast now, from `/content` and their stats.

Each class unlocks four skills between levels 11 and 14; a cast costs mana,
and the task-targeted ones (Burst of Flames, Brutal Smash, Pickpocket,
Backstab) act on one task. Whether a skill is castable is decided here —
`mp >= mana` — never left to the model.
"""

from dataclasses import dataclass
from typing import Any

from app.habitica.types import Skill, UserStats, skills_from_content

TARGET_LABELS = {
    "task": "on a task",
    "self": "on yourself",
    "party": "on the party",
    "tasks": "on all tasks",
}


@dataclass(frozen=True)
class AvailableSkill:
    skill: Skill
    castable: bool

    @property
    def needs_task(self) -> bool:
        return self.skill.target == "task"


def available_skills(stats: UserStats, content: dict[str, Any]) -> list[AvailableSkill]:
    """The user's class's skills unlocked at their level, cheapest first."""
    unlocked = [
        skill
        for skill in skills_from_content(content)
        if skill.class_name == stats.class_name and stats.lvl >= skill.level
    ]
    return [
        AvailableSkill(skill, castable=stats.mp >= skill.mana)
        for skill in sorted(unlocked, key=lambda s: (s.mana, s.level))
    ]


def find_skill(skills: list[AvailableSkill], reference: str) -> AvailableSkill | None:
    """By key (`fireball`) or name (`Burst of Flames`), case-insensitively."""
    wanted = reference.strip().lower()
    for available in skills:
        if wanted in (available.skill.key.lower(), available.skill.text.lower()):
            return available
    return None


def skill_line(available: AvailableSkill) -> str:
    """`Burst of Flames (fireball) — 10 MP, on a task: You summon XP…` for the prompt."""
    skill = available.skill
    where = TARGET_LABELS.get(skill.target, skill.target)
    return f"{skill.text} ({skill.key}) — {skill.mana} MP, {where}: {skill.notes}"
