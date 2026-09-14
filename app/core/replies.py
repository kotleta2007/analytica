"""What the core hands back to an adapter.

A `Reply` is platform-agnostic: the Telegram adapter turns it into a message
with an inline keyboard, a photo and a voice note; the CLI prints it, numbers
the buttons and draws the chart in the terminal.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

ChartKind = Literal["line", "bar", "heatmap"]


@dataclass(frozen=True)
class User:
    """Who is talking: the Habitica user id every table is keyed by."""

    id: str


@dataclass
class Button:
    label: str
    action: str  # e.g. "confirm_transcription"
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChartData:
    """Plain data; adapters decide how to draw it."""

    title: str
    x: list[date]
    series: dict[str, list[float]]
    kind: ChartKind = "line"


@dataclass
class Reply:
    text: str
    buttons: list[Button] = field(default_factory=list)
    chart: ChartData | None = None
    voice: bytes | None = None  # mp3 audio, when TTS is on for this reply
    # False for a reply that only echoes the user's own words back; the TTS policy skips it.
    speak: bool = True
