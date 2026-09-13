"""The two models the bot uses, by role rather than by id.

    strong  the default for conversation and for the coach
    cheap   summaries, transcript cleanup, anything that only needs to be fast

`user_settings.model` holds one of the two names; `/model` switches it. The
ids behind the names come from `LLM_STRONG_MODEL` / `LLM_CHEAP_MODEL`.
"""

from app.config import get_settings

STRONG = "strong"
CHEAP = "cheap"
MODEL_NAMES = (STRONG, CHEAP)


def model_id(name: str) -> str:
    """The OpenRouter id behind a role name; a wrong name is a programming error."""
    settings = get_settings()
    ids = {STRONG: settings.llm_strong_model, CHEAP: settings.llm_cheap_model}
    return ids[name]


def registry() -> dict[str, str]:
    return {name: model_id(name) for name in MODEL_NAMES}
