"""
Chart generation for the weekly report.

House style rules, so every week's post looks like it came from the same
publication:
  * One idea per chart.
  * The benchmark is always drawn, always grey, always dashed.
  * Gross is light, net is dark. The gap between them IS the story.
  * No 3D, no dual axes, no chart junk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import matplotlib

matplotlib.use("Agg")  # render to file; never try to open a window

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from config import REPORT_DIR

# Two palettes: the site serves whichever matches the reader's theme via a
# <picture> element. Inverting a light chart with a CSS filter was the earlier
# approach, and it worked, but it silently reverses the colour ramp -- the
# lightest bar became the darkest. Rendering twice keeps the meaning fixed:
# in each theme, the MOST IMPORTANT bar (out-of-sample, net) has the most
# contrast against the page.

LIGHT = {
    "in_sample": "#9ecae1",   # light  -- the flattering number
    "out_gross": "#4292c6",   # mid    -- honest, before costs
    "out_net":   "#08519c",   # dark   -- what you would actually have kept
    "bench":     "#888888",
    "zero":      "#c1272d",
    "ink":       "#16181D",
    "muted":     "#5B6169",
    "grid":      "#D8D4CD",
    "spine":     "#B9B4AB",
    "cycle":     ["#4292c6", "#E08A3C", "#3F9E6E", "#C1453B", "#8266B8"],
}

DARK = {
    "in_sample": "#2F5375",   # dark   -- least prominent on a dark page
    "out_gross": "#5590C8",
    "out_net":   "#93C3F2",   # bright -- most prominent, same meaning as above
    "bench":     "#8A9199",
    "zero":      "#E5665C",
    "ink":       "#E9EAEC",
    "muted":     "#969CA5",
    "grid":      "#2A2F36",
    "spine":     "#3A414A",
    "cycle":     ["#5590C8", "#E0A34A", "#4FBF92", "#F0806F", "#A78BE0"],
}


def _apply_theme(dark: bool) -> dict:
    """Set global rcParams for one theme and return its colour dict.

    Backgrounds are transparent in both themes so the page colour shows
    through -- that way a chart never sits on a slightly-wrong rectangle if
    the site palette is later adjusted.
    """
    c = DARK if dark else LIGHT
    plt.rcParams.update({
        "figure.dpi": 130,
        "savefig.dpi": 130,
        "font.size": 10,
        "figure.facecolor": "none",
        "axes.facecolor": "none",
        "savefig.facecolor": "none",
        "savefig.transparent": True,
        "text.color": c["ink"],
        "axes.labelcolor": c["muted"],
        "axes.edgecolor": c["spine"],
        "xtick.color": c["muted"],
        "ytick.color": c["muted"],
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": c["grid"],
        "grid.alpha": 0.9,
        "grid.linestyle": "-",
        "grid.linewidth": 0.6,
        "axes.prop_cycle": plt.cycler(color=c["cycle"]),
    })
    return c


def _title_block(ax, title: str, subtitle: str) -> None:
    """Draw a title with an optional subtitle underneath it, without overlap.

    matplotlib places a left-aligned title flush against the top of the axes,
    so a subtitle written at y=1.02 lands on top of it. Padding the title
    upward first reserves the gap the subtitle needs.
    """
    ax.set_title(title, fontsize=13, fontweight="bold", loc="left",
                 pad=26 if subtitle else 10)
    if subtitle:
        ax.text(0, 1.02, subtitle, transform=ax.transAxes,
                fontsize=8.5, color=plt.rcParams["axes.labelcolor"], va="bottom")


def summarise_universe(tickers, max_shown: int = 5) -> str:
    """Short label for a ticker list -- full names are unreadable in a subtitle."""
    tickers = list(tickers)
    if len(tickers) <= max_shown:
        return ", ".join(tickers)
    return f"{', '.join(tickers[:max_shown])} +{len(tickers) - max_shown} more"


def decay_curve(
    scores: Dict[str, Dict[str, float]],
    benchmark_sharpe: Optional[float] = None,
    title: str = "Indicator Sharpe decay",
    subtitle: str = "",
    outfile: Optional[Path] = None,
    dark: bool = False,
) -> Path:
    """THE chart of this project: three bars per indicator, side by side.

    Bar 1  in-sample, gross of costs      -- the number retail backtests quote
    Bar 2  out-of-sample, gross of costs  -- what survives unseen data
    Bar 3  out-of-sample, net of costs    -- what you would actually have kept

    A healthy indicator shows three similar bars. The usual result is a tall
    first bar and a third bar at or below zero, and the visual drop is far
    more persuasive than any table of numbers.

    `scores` maps indicator name -> dict with keys
    'in_sample_gross', 'out_sample_gross', 'out_sample_net'.
    """
    C = _apply_theme(dark)
    names = list(scores.keys())
    if not names:
        raise ValueError("no indicator scores to plot")

    x = np.arange(len(names))
    width = 0.26

    in_s = [scores[n].get("in_sample_gross", np.nan) for n in names]
    out_g = [scores[n].get("out_sample_gross", np.nan) for n in names]
    out_n = [scores[n].get("out_sample_net", np.nan) for n in names]

    fig, ax = plt.subplots(figsize=(9, 5))

    ax.bar(x - width, in_s, width, label="In-sample, gross", color=C["in_sample"])
    ax.bar(x, out_g, width, label="Out-of-sample, gross", color=C["out_gross"])
    ax.bar(x + width, out_n, width, label="Out-of-sample, NET of costs", color=C["out_net"])

    ax.axhline(0.0, color=C["zero"], linewidth=1.0, zorder=1)

    if benchmark_sharpe is not None and not np.isnan(benchmark_sharpe):
        ax.axhline(
            benchmark_sharpe, color=C["bench"], linestyle="--", linewidth=1.4,
            label=f"Buy & hold ({benchmark_sharpe:.2f})",
        )

    # Label each bar; readers should not have to estimate from the axis.
    for xs, vals in ((x - width, in_s), (x, out_g), (x + width, out_n)):
        for xi, v in zip(xs, vals):
            if np.isnan(v):
                continue
            ax.annotate(
                f"{v:.2f}", (xi, v), ha="center",
                va="bottom" if v >= 0 else "top",
                xytext=(0, 3 if v >= 0 else -3), textcoords="offset points",
                fontsize=8,
            )

    # Reserve vertical space at the top for the legend, so it cannot overlap
    # the tallest bar or the benchmark line.
    finite = [v for v in in_s + out_g + out_n if not np.isnan(v)]
    if benchmark_sharpe is not None and not np.isnan(benchmark_sharpe):
        finite.append(benchmark_sharpe)
    if finite:
        lo, hi = min(finite + [0.0]), max(finite + [0.0])
        span = max(hi - lo, 0.5)
        ax.set_ylim(lo - 0.12 * span, hi + 0.34 * span)

    ax.set_xticks(x)
    ax.set_xticklabels(names)
    ax.set_ylabel("Annualised Sharpe ratio")
    _title_block(ax, title, subtitle)
    ax.legend(frameon=False, fontsize=8, ncol=2, loc="upper center")

    fig.tight_layout()
    outfile = outfile or (REPORT_DIR / "decay_curve.png")
    fig.savefig(outfile, bbox_inches="tight")
    plt.close(fig)
    return outfile


def equity_curves(
    curves: Dict[str, pd.Series],
    title: str = "Growth of 1.00, net of costs",
    subtitle: str = "",
    benchmark_name: str = "HOLD",
    outfile: Optional[Path] = None,
    dark: bool = False,
) -> Path:
    """Net-of-cost equity curves, benchmark drawn in grey dashes."""
    C = _apply_theme(dark)
    fig, ax = plt.subplots(figsize=(9, 5))

    for name, series in curves.items():
        if name == benchmark_name:
            ax.plot(series.index, series.values, color=C["bench"], linestyle="--",
                    linewidth=1.6, label=f"{name} (benchmark)", zorder=1)
        else:
            ax.plot(series.index, series.values, linewidth=1.4, label=name, zorder=2)

    ax.axhline(1.0, color=C["zero"], linewidth=0.8, alpha=0.6)
    ax.set_yscale("log")  # log scale: a 10x and a 2x move look proportionate
    ax.set_ylabel("Value of 1.00 invested (log scale)")
    _title_block(ax, title, subtitle)
    ax.legend(frameon=False, fontsize=8)

    fig.tight_layout()
    outfile = outfile or (REPORT_DIR / "equity_curves.png")
    fig.savefig(outfile, bbox_inches="tight")
    plt.close(fig)
    return outfile


def cost_sensitivity(
    sensitivity: Dict[str, List[float]],
    cost_levels_bps: List[float],
    title: str = "How much cost does each indicator survive?",
    subtitle: str = "",
    outfile: Optional[Path] = None,
    dark: bool = False,
) -> Path:
    """Out-of-sample net Sharpe as trading costs rise from zero.

    Answers the question a skeptical reader will ask immediately: "your cost
    assumption is made up -- what if it's wrong?" Showing the whole curve is
    a stronger answer than defending one number. Where each line crosses zero
    is the break-even cost that indicator can tolerate.
    """
    C = _apply_theme(dark)
    fig, ax = plt.subplots(figsize=(9, 5))

    for name, sharpes in sensitivity.items():
        ax.plot(cost_levels_bps, sharpes, marker="o", markersize=4, linewidth=1.5, label=name)

    ax.axhline(0.0, color=C["zero"], linewidth=1.0)
    ax.set_xlabel("Round-trip cost assumption (basis points per side)")
    ax.set_ylabel("Out-of-sample Sharpe, net")
    _title_block(ax, title, subtitle)
    ax.legend(frameon=False, fontsize=8)

    fig.tight_layout()
    outfile = outfile or (REPORT_DIR / "cost_sensitivity.png")
    fig.savefig(outfile, bbox_inches="tight")
    plt.close(fig)
    return outfile


def live_equity(
    dates: List[str],
    nav: List[float],
    benchmark: List[Optional[float]],
    exposure: Optional[List[float]] = None,
    live_from: Optional[str] = None,
    starting_capital: float = 100_000.0,
    title: str = "Live paper portfolio",
    subtitle: str = "",
    outfile: Optional[Path] = None,
    dark: bool = False,
) -> Path:
    """The live book's NAV against an equal-weight buy & hold of the same names.

    Two extra pieces of context that a bare equity curve would hide:

    * A shaded band marking the period that was reconstructed when the book
      opened, versus the period recorded forward one day at a time. A reader
      should never have to take on trust which half is which.
    * A thin exposure track along the bottom. This strategy sits in cash most
      of the time, so comparing its return to a fully-invested benchmark
      without showing that would be misleading in the flattering direction.
    """
    C = _apply_theme(dark)

    x = [pd.Timestamp(d) for d in dates]
    fig, ax = plt.subplots(figsize=(9, 5))

    # Mark what was reconstructed at launch versus recorded forward. When
    # `live_from` is None NOTHING has been recorded live yet, and the entire
    # span must be shaded -- an un-shaded chart in that state would imply the
    # opposite of the truth.
    cut = pd.Timestamp(live_from) if live_from else x[-1]
    ax.axvspan(x[0], cut, color=C["grid"], alpha=0.45, linewidth=0, zorder=0)
    ax.axvline(cut, color=C["muted"], linewidth=1.0, linestyle=":", zorder=1)
    ax.annotate(
        "recorded live from here" if live_from
        else "shaded: reconstructed at launch — live recording begins here",
        xy=(cut, max(nav)), xytext=(-6 if not live_from else 6, -10),
        ha="right" if not live_from else "left",
        textcoords="offset points", fontsize=8, color=C["muted"], zorder=5)

    bench_x = [xi for xi, b in zip(x, benchmark) if b is not None]
    bench_y = [b for b in benchmark if b is not None]
    if bench_y:
        ax.plot(bench_x, bench_y, color=C["bench"], linestyle="--", linewidth=1.5,
                label="Buy & hold, same names", zorder=2)

    ax.plot(x, nav, color=C["out_net"], linewidth=1.9,
            label="Strategy (RSI, long only)", zorder=3)

    ax.axhline(starting_capital, color=C["zero"], linewidth=0.9, alpha=0.65, zorder=1)

    if exposure:
        # Exposure rides along the bottom on its own scale, deliberately faint:
        # it is context for the equity line, not a third headline series.
        twin = ax.twinx()
        twin.fill_between(x, 0, exposure, color=C["out_gross"], alpha=0.16,
                          linewidth=0, zorder=0)
        twin.set_ylim(0, 4.0)          # squashes the band into the lower quarter
        twin.set_yticks([])
        twin.spines[:].set_visible(False)
        ax.set_zorder(twin.get_zorder() + 1)
        ax.patch.set_visible(False)
        twin.annotate("shaded: share of the book actually invested",
                      xy=(0.015, 0.03), xycoords="axes fraction",
                      fontsize=7.5, color=C["muted"])

    ax.set_ylabel(f"Portfolio value")
    ax.yaxis.set_major_formatter(
        matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    _title_block(ax, title, subtitle)
    ax.legend(frameon=False, fontsize=8, loc="upper left")

    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    outfile = outfile or (REPORT_DIR / "live_equity.png")
    fig.savefig(outfile, bbox_inches="tight")
    plt.close(fig)
    return outfile
