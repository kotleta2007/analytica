"""The bot in a terminal, with zero Telegram.

    uv run python -m app.adapters.cli

Text goes to `text_message_received`, `/commands` to `command_received`, and
a bare number presses that button from the last reply. Charts are drawn with
plotext; voice replies are written to `data/voice/` and played with `ffplay`
or `afplay` when one is on PATH, otherwise the path is printed.
"""

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.analytics.charts import to_terminal
from app.config import get_settings
from app.core import outbound
from app.core.commands import parse_command
from app.core.harness import Harness
from app.core.replies import Button, Reply, User
from app.db.engine import dispose_engine
from app.habitica.client import client_from_settings

logger = logging.getLogger(__name__)

VOICE_DIR = Path("data/voice")
PLAYERS = (("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"), ("afplay",))
QUIT = ("/quit", "/exit")
PROMPT = "> "


@dataclass
class Cli:
    harness: Harness
    user: User
    voice_dir: Path = VOICE_DIR
    buttons: list[Button] = field(default_factory=list)
    voice_counter: int = 0

    async def handle_line(self, line: str) -> Reply:
        """Route one line of input to the right harness entry point."""
        stripped = line.strip()
        if stripped.isdigit() and 1 <= int(stripped) <= len(self.buttons):
            button = self.buttons[int(stripped) - 1]
            self.buttons = []  # an offer is pressed once
            return await self.harness.button_pressed(self.user, button.action, button.payload)
        command = parse_command(stripped)
        if command is not None:
            return await self.harness.command_received(self.user, *command)
        return await self.harness.text_message_received(self.user, stripped)

    async def show(self, reply: Reply) -> str:
        """Render a reply for the terminal; returns what gets printed."""
        parts = [reply.text]
        if reply.chart is not None:
            parts.append(to_terminal(reply.chart))
        if reply.voice is not None:
            path = self.save_voice(reply.voice)
            parts.append(f"[voice] {path}")
            await play(path)
        self.buttons = list(reply.buttons)
        if self.buttons:
            numbered = "  ".join(f"[{n}] {b.label}" for n, b in enumerate(self.buttons, start=1))
            parts.append(numbered + "\n(type a number to press)")
        return "\n".join(parts)

    def save_voice(self, audio: bytes) -> Path:
        self.voice_dir.mkdir(parents=True, exist_ok=True)
        self.voice_counter += 1
        path = self.voice_dir / f"reply-{self.voice_counter:04d}.ogg"
        path.write_bytes(audio)
        return path

    async def deliver(self, user: User, reply: Reply) -> None:
        """Outbound hook: a scheduled message lands in the terminal like any reply."""
        print("\n" + await self.show(reply) + "\n" + PROMPT, end="", flush=True)

    async def run(self) -> None:
        outbound.register(self.deliver)
        print("Habitica Analytica. /help for commands, /quit to leave.")
        try:
            while True:
                try:
                    line = await asyncio.to_thread(input, PROMPT)
                except (EOFError, KeyboardInterrupt):
                    break
                if line.strip() in QUIT:
                    break
                if not line.strip():
                    continue
                print(await self.show(await self.handle_line(line)))
        finally:
            outbound.unregister()


async def play(path: Path) -> None:
    for player in PLAYERS:
        if shutil.which(player[0]) is None:
            continue
        process = await asyncio.create_subprocess_exec(*player, str(path))
        await process.wait()
        return
    logger.debug("no audio player on PATH; %s not played", path)


async def migrate() -> None:
    """Same as `alembic upgrade head`; alembic's env.py owns its own event loop."""
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level="WARNING", format="%(levelname)s %(name)s %(message)s")
    await migrate()
    async with client_from_settings() as client:
        cli = Cli(harness=Harness(habitica=client), user=User(settings.habitica_user_id))
        await cli.run()
    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
