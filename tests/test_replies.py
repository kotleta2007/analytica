from datetime import date

from app.core.replies import Button, ChartData, Reply, User


def test_reply_defaults_are_empty_not_shared() -> None:
    first = Reply(text="a")
    second = Reply(text="b")
    first.buttons.append(Button(label="x", action="x"))

    assert second.buttons == []
    assert second.chart is None
    assert second.voice is None


def test_button_payload_defaults_to_an_empty_dict() -> None:
    assert Button(label="✅", action="confirm").payload == {}


def test_chart_data_defaults_to_a_line_chart() -> None:
    chart = ChartData(title="gp", x=[date(2026, 9, 1)], series={"gp": [1.0]})

    assert chart.kind == "line"


def test_user_is_hashable_and_compares_by_id() -> None:
    assert User("abc") == User("abc")
    assert {User("abc"), User("abc")} == {User("abc")}
