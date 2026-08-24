"""
Weekly report generator.

Produces the same markdown structure every week, so the publication has a
recognisable format and week-to-week results are directly comparable. The
format is deliberately fixed: readers should be able to skim to "Verdict"
without re-learning the layout, and you should be able to write the post in
ten minutes rather than redesigning it each time.

Markdown, not PDF, because it pastes cleanly into Substack, LinkedIn, and
GitHub without conversion. Convert to PDF later if a specific venue needs it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from config import REPORT_DIR, CostModel

# --------------------------------------------------------------------------
# Verdict logic -- the one judgement call in the report, made by rule
# --------------------------------------------------------------------------

SURVIVED = "SURVIVED"
WEAKENED = "WEAKENED"
FAILED = "FAILED"

VERDICT_MEANING = {
    SURVIVED: "positive out-of-sample Sharpe after costs, AND beat buy & hold",
    WEAKENED: "still positive after costs out-of-sample, but lost to buy & hold",
    FAILED: "out-of-sample Sharpe was zero or negative once costs were applied",
}


def classify(out_sample_net: float, benchmark_net: float) -> str:
    """Assign a verdict by rule, not by eye.

    Fixing the thresholds in code means the standard cannot drift week to week
    to suit the result -- which is exactly the failure mode this project is
    about. If the rule is wrong, change it here, in public, once.
    """
    if np.isnan(out_sample_net):
        return FAILED
    if out_sample_net <= 0:
        return FAILED
    if out_sample_net > benchmark_net:
        return SURVIVED
    return WEAKENED


def _fmt(value: float, kind: str = "num") -> str:
    """Format a number for a table cell, or an em-dash if it is missing."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "int":
        return f"{int(value):,}"
    return f"{value:.2f}"


def _plain_english_verdict(name: str, s: Dict[str, float]) -> str:
    """One sentence a non-technical reader can follow, per indicator."""
    return (
        f"**{name}** posted a {_fmt(s['in_sample_gross'])} Sharpe in-sample, "
        f"{_fmt(s['out_sample_gross'])} out-of-sample, and "
        f"{_fmt(s['out_sample_net'])} once trading costs were applied "
        f"({_fmt(s.get('num_trades', np.nan), 'int')} trades, "
        f"{_fmt(s.get('cost_paid', np.nan), 'pct')} of notional paid away)."
    )


# --------------------------------------------------------------------------
# Report body
# --------------------------------------------------------------------------

def build_report(
    scores: Dict[str, Dict[str, float]],
    descriptions: Dict[str, str],
    benchmark: Dict[str, float],
    costs: CostModel,
    universe: List[str],
    period_label: str,
    split_label: str,
    charts: Optional[Dict[str, Path]] = None,
    walk_forward: Optional[Dict[str, Dict[str, float]]] = None,
    week_number: Optional[int] = None,
) -> str:
    """Assemble the full markdown report as a single string."""
    charts = charts or {}
    today = dt.date.today()
    week_str = f"Week {week_number}" if week_number else today.strftime("Week of %d %B %Y")
    bench_net = benchmark.get("out_sample_net", np.nan)

    verdicts = {n: classify(s.get("out_sample_net", np.nan), bench_net) for n, s in scores.items()}
    survived = [n for n, v in verdicts.items() if v == SURVIVED]
    failed = [n for n, v in verdicts.items() if v == FAILED]

    L: List[str] = []
    add = L.append

    # ---- Header -------------------------------------------------------
    add(f"# Margin & Co. — {week_str}")
    add("")
    add(f"*Testing whether popular retail trading indicators survive realistic trading costs.*")
    add("")
    add(f"**Universe:** {', '.join(universe)}  ")
    add(f"**Period:** {period_label}  ")
    add(f"**Validation:** {split_label}  ")
    add(f"**Cost assumption:** {costs.total_bps_per_side:.0f} bps per side "
        f"({costs.half_spread_bps:.0f} spread + {costs.commission_bps:.0f} commission "
        f"+ {costs.slippage_bps:.0f} slippage)")
    add("")
    add("---")
    add("")

    # ---- Bottom line first --------------------------------------------
    add("## The bottom line")
    add("")
    if survived:
        add(f"Of {len(scores)} indicators tested, **{len(survived)} beat buy & hold "
            f"out-of-sample after costs**: {', '.join(survived)}.")
    else:
        add(f"Of {len(scores)} indicators tested, **none beat buy & hold "
            f"out-of-sample after costs.**")
    if failed:
        add("")
        add(f"{', '.join(failed)} produced a zero or negative Sharpe once costs "
            f"were applied — meaning the strategy would have lost money net of "
            f"what it cost to trade it.")
    add("")
    add(f"Buy & hold, the benchmark, returned a Sharpe of **{_fmt(bench_net)}** "
        f"over the same out-of-sample window.")
    add("")

    # ---- The chart ----------------------------------------------------
    if "decay" in charts:
        add("## The decay curve")
        add("")
        add(f"![Sharpe decay by indicator]({Path(charts['decay']).name})")
        add("")
        add("*Left bar: what the indicator looked like on the data used to design it. "
            "Middle bar: the same rule on data it had never seen. "
            "Right bar: after paying to trade. The gap between the first and last "
            "bar is the number retail backtests do not show you.*")
        add("")

    # ---- Results table ------------------------------------------------
    add("## Results")
    add("")
    add("| Indicator | In-sample Sharpe (gross) | Out-of-sample Sharpe (gross) | "
        "Out-of-sample Sharpe (net) | Trades | Cost paid | Max drawdown | Win rate | Verdict |")
    add("|---|---:|---:|---:|---:|---:|---:|---:|:---:|")

    for name, s in scores.items():
        add(
            f"| {name} "
            f"| {_fmt(s.get('in_sample_gross'))} "
            f"| {_fmt(s.get('out_sample_gross'))} "
            f"| {_fmt(s.get('out_sample_net'))} "
            f"| {_fmt(s.get('num_trades'), 'int')} "
            f"| {_fmt(s.get('cost_paid'), 'pct')} "
            f"| {_fmt(s.get('max_drawdown_net'), 'pct')} "
            f"| {_fmt(s.get('win_rate_net'), 'pct')} "
            f"| {verdicts[name]} |"
        )

    add(f"| *Buy & hold* | {_fmt(benchmark.get('in_sample_gross'))} "
        f"| {_fmt(benchmark.get('out_sample_gross'))} "
        f"| {_fmt(bench_net)} "
        f"| {_fmt(benchmark.get('num_trades'), 'int')} "
        f"| {_fmt(benchmark.get('cost_paid'), 'pct')} "
        f"| {_fmt(benchmark.get('max_drawdown_net'), 'pct')} "
        f"| — | *benchmark* |")
    add("")
    for label, meaning in VERDICT_MEANING.items():
        add(f"- **{label}** — {meaning}")
    add("")

    # ---- Per-indicator narrative --------------------------------------
    add("## What happened, indicator by indicator")
    add("")
    for name, s in scores.items():
        add(f"### {name} — {verdicts[name]}")
        add("")
        add(f"*{descriptions.get(name, '')}*")
        add("")
        add(_plain_english_verdict(name, s))
        add("")

    # ---- Walk-forward -------------------------------------------------
    if walk_forward:
        add("## Walk-forward check")
        add("")
        add("One 70/30 split gives one reading, and one reading can be luck. "
            "Below, each indicator is re-tested across rolling three-year "
            "training windows with the following year held out — many "
            "independent out-of-sample readings instead of one.")
        add("")
        add("| Indicator | Windows | Mean OOS Sharpe (net) | Windows positive | Worst window |")
        add("|---|---:|---:|---:|---:|")
        for name, wf in walk_forward.items():
            add(f"| {name} | {_fmt(wf.get('n_windows'), 'int')} "
                f"| {_fmt(wf.get('mean_sharpe_net'))} "
                f"| {_fmt(wf.get('pct_positive'), 'pct')} "
                f"| {_fmt(wf.get('worst_sharpe_net'))} |")
        add("")

    # ---- Other charts -------------------------------------------------
    if "equity" in charts:
        add("## Growth of 1.00, net of costs")
        add("")
        add(f"![Equity curves]({Path(charts['equity']).name})")
        add("")
    if "sensitivity" in charts:
        add("## How wrong could the cost assumption be?")
        add("")
        add(f"![Cost sensitivity]({Path(charts['sensitivity']).name})")
        add("")
        add("*Where each line crosses zero is the highest trading cost that "
            "indicator could tolerate and still break even.*")
        add("")

    # ---- Method and limitations ---------------------------------------
    add("## Method")
    add("")
    add("1. Daily split- and dividend-adjusted bars from Yahoo Finance.")
    add("2. Each indicator emits a target position of +1 (long), 0 (flat), or -1 (short).")
    add("3. **Positions are shifted forward one day before returns are applied.** "
        "A signal computed from Monday's close can only be traded at Monday's "
        "close, so the first return it can earn is Monday-to-Tuesday. Skipping "
        "this step is the most common backtest bug and typically adds one to "
        "three points of Sharpe that do not exist.")
    add("4. Costs are charged on turnover: cost = |change in position| × "
        f"{costs.total_bps_per_side:.0f} bps. A long-to-short flip pays twice.")
    add("5. The training period is used to design and sanity-check the rules. "
        "The test period is measured once.")
    add("")
    add("## Limitations")
    add("")
    add("Stated every week, because they do not go away:")
    add("")
    add("- **Survivorship bias.** The universe uses *current* index membership. "
        "Companies that were delisted or dropped from the index are absent, "
        "which flatters absolute returns. The in-sample versus out-of-sample "
        "*comparison* is affected much less, since both halves share the bias.")
    add("- **One cost assumption, applied uniformly.** Real spreads vary by "
        "stock, by day, and widen exactly when you most want to trade. The "
        "sensitivity chart is the honest response to this.")
    add("- **No position sizing, leverage, or risk limits.** Every position is "
        "the same size. Real execution would size by volatility.")
    add("- **Daily bars only.** Intraday signals cannot be evaluated here — "
        "in particular, true VWAP is an intraday measure and what is tested "
        "here is a rolling volume-weighted moving average. That is a different "
        "thing and is labelled as such.")
    add("- **Long/short with no borrow cost.** Shorting is assumed free. It is not.")
    add("")
    add("---")
    add("")
    add(f"*Generated {today.isoformat()} by the Margin & Co. pipeline. "
        f"Code and data are open: every number above can be reproduced by "
        f"running `python run_pipeline.py`.*")

    return "\n".join(L)


def write_report(markdown: str, outfile: Optional[Path] = None, week_number: Optional[int] = None) -> Path:
    """Write the report to disk with a dated, sortable filename."""
    if outfile is None:
        stamp = dt.date.today().isoformat()
        suffix = f"_week{week_number:02d}" if week_number else ""
        outfile = REPORT_DIR / f"{stamp}{suffix}_margin_and_co.md"

    outfile.write_text(markdown, encoding="utf-8")
    return outfile
