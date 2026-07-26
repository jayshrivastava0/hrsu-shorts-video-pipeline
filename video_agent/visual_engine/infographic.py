"""Matplotlib infographic renderer with HRSU brand styling."""
import logging
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from video_agent.config import (
    BRAND_GOLD, BRAND_DARK_NAVY, BRAND_NAVY_2, BRAND_TEXT_LIGHT, BRAND_TEXT_MUTED,
)

log = logging.getLogger(__name__)

plt.rcParams.update({
    "axes.facecolor": BRAND_DARK_NAVY,
    "figure.facecolor": BRAND_DARK_NAVY,
    "axes.edgecolor": BRAND_TEXT_MUTED,
    "axes.labelcolor": BRAND_TEXT_LIGHT,
    "xtick.color": BRAND_TEXT_LIGHT,
    "ytick.color": BRAND_TEXT_LIGHT,
    "font.size": 22,
})


def _setup_fig(resolution: tuple[int, int]):
    w, h = resolution
    dpi = 100
    fig = plt.figure(figsize=(w / dpi, h / dpi), dpi=dpi,
                     facecolor=BRAND_DARK_NAVY)
    return fig


def _add_title(fig, title: str):
    fig.text(0.5, 0.92, title, ha="center", va="top",
             color=BRAND_GOLD, fontsize=42, weight="bold")


def _add_footer(fig):
    fig.text(0.5, 0.04, "hrsuindore.com", ha="center",
             color=BRAND_TEXT_MUTED, fontsize=18)


def _bar(fig, data: dict):
    # Generous left/right/bottom margins so rotated labels never clip the canvas.
    ax = fig.add_axes([0.16, 0.32, 0.72, 0.50])
    labels = [str(l) for l in data.get("labels", [])]
    values = data.get("values", [])
    # Cap label length so long phrases don't run off the figure even at the
    # smaller fontsize. 18 chars is what fits comfortably at 30deg rotation.
    labels = [(l if len(l) <= 18 else l[:17] + "…") for l in labels]
    colors = [BRAND_GOLD if v == max(values) else BRAND_TEXT_MUTED for v in values]
    bars = ax.bar(range(len(values)), values, color=colors, edgecolor="none", width=0.6)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=15)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.set_xlim(-0.6, len(values) - 0.4)
    ax.set_ylim(0, max(values) * 1.20)
    for i, v in enumerate(values):
        ax.text(i, v + max(values) * 0.02, str(v), ha="center", va="bottom",
                color=BRAND_TEXT_LIGHT, fontsize=26, weight="bold")


def _callout_stat(fig, data: dict):
    fig.text(0.5, 0.55, str(data.get("value", "")),
             ha="center", va="center", color=BRAND_GOLD,
             fontsize=200, weight="bold")
    fig.text(0.5, 0.30, str(data.get("label", "")),
             ha="center", va="center", color=BRAND_TEXT_LIGHT,
             fontsize=36)


def _comparison(fig, data: dict):
    left_lbl = data.get("left_label", "Without")
    right_lbl = data.get("right_label", "With")
    left_val = str(data.get("left_value", "—"))
    right_val = str(data.get("right_value", "—"))
    fig.text(0.27, 0.6, left_val, ha="center", color=BRAND_TEXT_MUTED,
             fontsize=120, weight="bold")
    fig.text(0.73, 0.6, right_val, ha="center", color=BRAND_GOLD,
             fontsize=120, weight="bold")
    fig.text(0.27, 0.4, left_lbl, ha="center", color=BRAND_TEXT_MUTED, fontsize=32)
    fig.text(0.73, 0.4, right_lbl, ha="center", color=BRAND_TEXT_LIGHT, fontsize=32)


def _flow(fig, data: dict):
    steps = data.get("steps", [])[:5]
    if not steps:
        return
    ax = fig.add_axes([0.05, 0.35, 0.9, 0.30])
    ax.set_xlim(0, len(steps))
    ax.set_ylim(0, 1)
    ax.axis("off")
    for i, step in enumerate(steps):
        ax.add_patch(plt.Rectangle((i + 0.05, 0.3), 0.9, 0.4,
                                   facecolor=BRAND_NAVY_2, edgecolor=BRAND_GOLD, linewidth=3))
        ax.text(i + 0.5, 0.5, step, ha="center", va="center",
                color=BRAND_TEXT_LIGHT, fontsize=22, weight="bold")
        if i < len(steps) - 1:
            ax.annotate("", xy=(i + 1.05, 0.5), xytext=(i + 0.95, 0.5),
                        arrowprops=dict(arrowstyle="->", color=BRAND_GOLD, lw=3))


def _line(fig, data: dict):
    ax = fig.add_axes([0.12, 0.20, 0.76, 0.58])
    x = data.get("x", [])
    y = data.get("y", [])
    ax.plot(x, y, color=BRAND_GOLD, linewidth=4, marker="o", markersize=10)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)


_DISPATCH = {
    "bar": _bar, "callout_stat": _callout_stat, "comparison": _comparison,
    "flow": _flow, "line": _line,
}


def render_infographic(output_path: Path, *, chart_type: str, title: str = "",
                       data: dict | None = None,
                       resolution: tuple[int, int] = (1080, 1920),
                       seed: int | None = None) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if seed is not None:
        np.random.seed(seed)
    fig = _setup_fig(resolution)
    if title:
        _add_title(fig, title)
    fn = _DISPATCH.get(chart_type, _callout_stat)
    fn(fig, data or {})
    _add_footer(fig)
    fig.savefig(output_path, dpi=100, facecolor=BRAND_DARK_NAVY,
                bbox_inches=None, pad_inches=0)
    plt.close(fig)
    return output_path
