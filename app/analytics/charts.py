"""Draw a `ChartData`.

One chart object, one renderer per surface: `to_png` for Telegram (seaborn on
a headless matplotlib), `to_terminal` for the CLI (plotext). The stat series
that feed them arrive in P07.
"""

import io

import matplotlib
import plotext as plt

matplotlib.use("Agg")  # before pyplot: no display on the VPS

import matplotlib.pyplot as pyplot  # noqa: E402
import seaborn as sns  # noqa: E402

from app.core.replies import ChartData  # noqa: E402

TERMINAL_WIDTH = 80
TERMINAL_HEIGHT = 18
DATE_FORM = "d/m"
PNG_SIZE = (8, 4)  # inches at 150 dpi: sharp on a phone, ~50 KB
PNG_DPI = 150


def to_png(chart: ChartData) -> bytes:
    sns.set_theme(style="whitegrid")
    figure, axes = pyplot.subplots(figsize=PNG_SIZE)
    try:
        if chart.kind == "heatmap":
            names = list(chart.series)
            sns.heatmap(
                [chart.series[name] for name in names],
                ax=axes,
                cmap="viridis",
                xticklabels=[day.strftime("%d/%m") for day in chart.x],
                yticklabels=names,
            )
        else:
            for name, values in chart.series.items():
                if chart.kind == "bar":
                    axes.bar(chart.x, values, label=name, alpha=0.8)
                else:
                    axes.plot(chart.x, values, label=name, marker="o", markersize=3)
            axes.legend()
            figure.autofmt_xdate()
        axes.set_title(chart.title)
        figure.tight_layout()
        buffer = io.BytesIO()
        figure.savefig(buffer, format="png", dpi=PNG_DPI)
        return buffer.getvalue()
    finally:
        pyplot.close(figure)


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
