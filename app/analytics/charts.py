"""Draw a `ChartData`.

One chart object, one renderer per surface: `to_terminal` for the CLI. The
stat series that feed it arrive in P07 together with the PNG renderer.
"""

import plotext as plt

from app.core.replies import ChartData

TERMINAL_WIDTH = 80
TERMINAL_HEIGHT = 18
DATE_FORM = "d/m"


def to_terminal(
    chart: ChartData, width: int = TERMINAL_WIDTH, height: int = TERMINAL_HEIGHT
) -> str:
    """The chart as text with ANSI colours, ready to print."""
    plt.clear_figure()
    plt.theme("clear")
    plt.plotsize(width, height)
    plt.title(chart.title)
    labels = [day.strftime("%d/%m") for day in chart.x]
    if chart.kind == "heatmap":
        names = list(chart.series)
        plt.matrix_plot([chart.series[name] for name in names])
        plt.yticks(list(range(1, len(names) + 1)), names)
        plt.xticks(list(range(1, len(labels) + 1)), labels)
    else:
        plt.date_form(DATE_FORM)
        draw = plt.bar if chart.kind == "bar" else plt.plot
        for name, values in chart.series.items():
            draw(labels, values, label=name)
    return plt.build()
