"""Loaders for the recorded Habitica responses under tests/fixtures/habitica/."""

import json
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "habitica"


def load_envelope(name: str) -> dict[str, Any]:
    """The full `{"success": true, "data": ...}` body, as the API sends it."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def load_data(name: str) -> Any:
    return load_envelope(name)["data"]
