"""The CLI adapter drives the whole core with fakes: the integration harness."""

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.adapters import cli as cli_module
from app.adapters.cli import Cli
from app.analytics.charts import to_terminal
from app.core.harness import Harness
from app.core.i18n import t
from app.core.replies import Button, ChartData, Reply, User
from app.habitica.types import task_from_json
from tests.fakes.habitica import FakeHabiticaClient
from tests.habitica_fixtures import load_data

USER = User("00000000-0000-4000-8000-000000000001")
NOW = datetime(2026, 9, 12, 12, 30, tzinfo=UTC)


@pytest.fixture
def fake_client() -> FakeHabiticaClient:
    return FakeHabiticaClient(tasks=[task_from_json(item) for item in load_data("tasks")])


@pytest.fixture
async def cli(db_path: Path, tmp_path: Path, fake_client: FakeHabiticaClient) -> Cli:
    harness = Harness(habitica=fake_client, clock=lambda: NOW, tz=ZoneInfo("Europe/Zurich"))
    return Cli(harness=harness, user=USER, voice_dir=tmp_path / "voice")


async def test_language_then_stats_in_russian(cli: Cli, fake_client: FakeHabiticaClient) -> None:
    await cli.handle_line("/language ru")
    reply = await cli.handle_line("/stats")
    shown = await cli.show(reply)

    user = fake_client.user
    assert shown.startswith(f"❤️ {user.hp:.0f}/{user.max_hp}")
    assert f"Уровень {user.lvl}, {user.class_name} · на 14:30" in shown
    assert fake_client.called("get_user") == [None]


async def test_free_text_gets_the_no_llm_line(cli: Cli) -> None:
    reply = await cli.handle_line("done with workout")

    assert reply.text == t("no_llm_yet", "en")


async def test_buttons_are_numbered_and_a_number_presses_one(cli: Cli) -> None:
    reply = Reply(
        "Delete Workout?",
        buttons=[Button("✅ Do it", "confirm", {"id": 1}), Button("❌ Cancel", "cancel")],
    )
    shown = await cli.show(reply)

    assert shown == "Delete Workout?\n[1] ✅ Do it  [2] ❌ Cancel\n(type a number to press)"

    pressed = await cli.handle_line("2")
    assert pressed.text == t("unknown_button", "en")  # the core has no actions yet
    assert cli.buttons == []  # an offer is pressed once


async def test_a_number_without_buttons_is_plain_text(cli: Cli) -> None:
    reply = await cli.handle_line("1")

    assert reply.text == t("no_llm_yet", "en")


async def test_chart_is_drawn_in_the_terminal(cli: Cli) -> None:
    chart = ChartData(
        title="gold",
        x=[date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)],
        series={"gp": [10.0, 12.5, 11.0]},
    )
    shown = await cli.show(Reply("Your gold", chart=chart))

    assert shown.startswith("Your gold\n")
    assert "gold" in shown and "01/09" in shown and "03/09" in shown


@pytest.mark.parametrize("kind", ["line", "bar", "heatmap"])
def test_every_chart_kind_renders(kind: str) -> None:
    chart = ChartData(
        title=kind,
        x=[date(2026, 9, 1), date(2026, 9, 2)],
        series={"a": [1.0, 2.0], "b": [2.0, 1.0]},
        kind=kind,  # type: ignore[arg-type]
    )
    out = to_terminal(chart, width=40, height=10)

    assert kind in out
    assert len(out.splitlines()) >= 8


async def test_voice_is_saved_and_played_when_a_player_exists(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    played: list[Path] = []

    async def fake_play(path: Path) -> None:
        played.append(path)

    monkeypatch.setattr(cli_module, "play", fake_play)
    shown = await cli.show(Reply("Hi", voice=b"OggS-fake"))

    saved = cli.voice_dir / "reply-0001.ogg"
    assert saved.read_bytes() == b"OggS-fake"
    assert shown == f"Hi\n[voice] {saved}"
    assert played == [saved]


async def test_play_without_a_player_only_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli_module.shutil, "which", lambda name: None)

    await cli_module.play(tmp_path / "x.ogg")  # no player found, no exception
