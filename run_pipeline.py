"""
Margin & Co. — main pipeline.

Run this once a week. It loads data, runs every indicator through the
train/test and walk-forward machinery, draws the charts, and writes the
markdown report ready to publish.

    python run_pipeline.py                      # smoke test: AAPL only
    python run_pipeline.py --universe sp500     # the S&P sample
    python run_pipeline.py --universe all       # S&P + FTSE sample
    python run_pipeline.py --tickers MSFT,SHEL.L
    python run_pipeline.py --no-cache           # force a fresh download
    python run_pipeline.py --week 1             # stamp the report as Week 1

Every step prints what it is doing. If a number in the report looks wrong,
the console output tells you which stage to go and look at.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from backtest import metrics as m
from backtest.engine import buy_and_hold, price_returns, run_backtest
from backtest.splits import Split, describe_splits, simple_split, walk_forward_windows
from config import (
    DEFAULT_COSTS,
    FTSE350_SAMPLE,
    REPORT_DIR,
    SMOKE_TEST_TICKERS,
    SP500_SAMPLE,
    CostModel,
)
from data import universe
from data.loader import describe, load_universe
from indicators import INDICATORS
from reports import charts, data, weekly

def _universe(name: str) -> List[str]:
    """Resolve a universe name to tickers.

    The `full-*` sets are the real index constituents, fetched and cached by
    data/universe.py. The small sets remain for quick iteration -- a 15-ticker
    run finishes in seconds, which matters when you are changing an indicator.
    """
    if name == "smoke":
        return SMOKE_TEST_TICKERS
    if name == "sample":
        return SP500_SAMPLE + FTSE350_SAMPLE
    if name == "sp500":
        return universe.load("sp500")
    if name == "ftse":
        return universe.load("ftse350")
    if name == "all":
        return universe.load("sp500") + universe.load("ftse350")
    raise ValueError(f"unknown universe: {name}")


UNIVERSE_NAMES = ["smoke", "sample", "sp500", "ftse", "all"]


# --------------------------------------------------------------------------
# Portfolio construction
# --------------------------------------------------------------------------

def _equal_weight(streams: List[pd.Series]) -> pd.Series:
    """Combine per-ticker return streams into one equal-weighted portfolio.

    Equal weight, rebalanced daily. Crude, but transparent and impossible to
    accidentally bias -- market-cap weighting would need point-in-time share
    counts, which this project does not have.

    Tickers with no data on a given day contribute nothing, rather than being
    forward-filled into a fake zero-return position.
    """
    if not streams:
        return pd.Series(dtype=float)
    frame = pd.concat(streams, axis=1)
    return frame.mean(axis=1, skipna=True)


def run_indicator(
    data_frames: Dict[str, pd.DataFrame],
    name: str,
    signal_fn,
    costs: CostModel,
) -> Tuple[pd.Series, pd.Series, pd.Series, Dict[str, float]]:
    """Run one indicator across the whole universe.

    Returns (gross_returns, net_returns, mean_positions, aggregate_stats) for
    the equal-weighted portfolio of per-ticker strategies.
    """
    gross_streams, net_streams, pos_streams = [], [], []
    total_trades, total_cost = 0, []

    for ticker, df in data_frames.items():
        positions = signal_fn(df)
        result = run_backtest(df, positions, name=name, ticker=ticker, costs=costs)

        gross_streams.append(result.gross_returns.rename(ticker))
        net_streams.append(result.net_returns.rename(ticker))
        pos_streams.append(result.positions.rename(ticker))
        total_trades += result.metrics_net["num_trades"]
        total_cost.append(result.total_cost_paid)

    gross = _equal_weight(gross_streams)
    net = _equal_weight(net_streams)
    positions = _equal_weight(pos_streams)

    stats = {
        "num_trades": total_trades,
        "cost_paid": float(np.mean(total_cost)) if total_cost else np.nan,
    }
    return gross, net, positions, stats


def score_on_split(
    gross: pd.Series,
    net: pd.Series,
    positions: pd.Series,
    split: Split,
    extra: Optional[Dict[str, float]] = None,
) -> Dict[str, float]:
    """Slice a portfolio's returns into train/test and score both halves.

    The three series can differ by a row at the front -- raw asset returns
    keep the leading NaN that `pct_change` produces, while backtested returns
    drop it. Align them first so a boolean mask built from one always fits
    the others.
    """
    idx = gross.index.intersection(net.index).intersection(positions.index)
    gross, net, positions = gross.loc[idx], net.loc[idx], positions.loc[idx]
    train, test = split.train_mask(idx), split.test_mask(idx)

    out = dict(extra or {})
    out["in_sample_gross"] = m.sharpe_ratio(gross[train])
    out["in_sample_net"] = m.sharpe_ratio(net[train])
    out["out_sample_gross"] = m.sharpe_ratio(gross[test])
    out["out_sample_net"] = m.sharpe_ratio(net[test])
    out["max_drawdown_net"] = m.max_drawdown(net[test])
    out["win_rate_net"] = m.win_rate(net[test], positions[test])
    out["total_return_net"] = m.total_return(net[test])
    return out


def run_walk_forward(
    gross: pd.Series,
    net: pd.Series,
    windows: List[Split],
) -> Dict[str, float]:
    """Score an indicator across every rolling out-of-sample window.

    The headline is not the mean Sharpe -- it is `pct_positive`. A strategy
    with a 0.4 mean Sharpe driven by one spectacular year and four flat ones
    is a very different proposition from one that earns 0.4 every year.
    """
    sharpes = []
    for w in windows:
        test = w.test_mask(net.index)
        if test.sum() < 60:
            continue
        sharpes.append(m.sharpe_ratio(net[test]))

    sharpes = [s for s in sharpes if not np.isnan(s)]
    if not sharpes:
        return {"n_windows": 0, "mean_sharpe_net": np.nan,
                "pct_positive": np.nan, "worst_sharpe_net": np.nan}

    arr = np.array(sharpes)
    return {
        "n_windows": len(arr),
        "mean_sharpe_net": float(arr.mean()),
        "pct_positive": float((arr > 0).mean()),
        "worst_sharpe_net": float(arr.min()),
    }


def run_cost_sensitivity(
    data_frames: Dict[str, pd.DataFrame],
    split: Split,
    levels_bps: List[float],
) -> Dict[str, List[float]]:
    """Re-run every indicator at several cost assumptions.

    Pre-empts the obvious objection: "your 8bp number is made up." Showing the
    whole curve is a better answer than defending one point on it.
    """
    sensitivity: Dict[str, List[float]] = {name: [] for name in INDICATORS}

    for bps in levels_bps:
        costs = CostModel(half_spread_bps=bps, commission_bps=0.0, slippage_bps=0.0)
        for name, (fn, _) in INDICATORS.items():
            _, net, _, _ = run_indicator(data_frames, name, fn, costs)
            test = split.test_mask(net.index)
            sensitivity[name].append(m.sharpe_ratio(net[test]))

    return sensitivity


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def render_themed(chart_fn, stem: str, **kwargs) -> "Path":
    """Render one chart twice -- light and dark -- and return the light path.

    The website serves whichever matches the reader's system theme via a
    <picture> element, so a chart never appears as a bright white rectangle
    on a dark page (or the reverse).
    """
    light = chart_fn(outfile=REPORT_DIR / f"{stem}.png", dark=False, **kwargs)
    chart_fn(outfile=REPORT_DIR / f"{stem}_dark.png", dark=True, **kwargs)
    return light


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Margin & Co. weekly backtest pipeline")
    parser.add_argument("--universe", choices=UNIVERSE_NAMES, default="smoke",
                        help="ticker set: smoke (AAPL), sample (15), sp500, ftse, all (848)")
    parser.add_argument("--tickers", type=str, default=None,
                        help="comma-separated tickers, overrides --universe")
    parser.add_argument("--start", type=str, default=None, help="start date YYYY-MM-DD")
    parser.add_argument("--no-cache", action="store_true", help="force fresh download")
    parser.add_argument("--week", type=int, default=None, help="week number for the report")
    parser.add_argument("--min-history-years", type=int, default=10,
                        help="drop tickers with less history than this (default 10)")
    parser.add_argument("--skip-sensitivity", action="store_true",
                        help="skip the cost sensitivity sweep (it is the slow step)")
    args = parser.parse_args(argv)

    tickers = ([t.strip() for t in args.tickers.split(",")] if args.tickers
               else _universe(args.universe))
    costs = DEFAULT_COSTS

    print("=" * 72)
    print("MARGIN & CO. — weekly pipeline")
    print("=" * 72)

    # -- 1. Data -------------------------------------------------------
    print(f"\n[1/6] Loading {len(tickers)} ticker(s)...")
    kwargs = {"use_cache": not args.no_cache}
    if args.start:
        kwargs["start"] = args.start
    data_frames = load_universe(tickers, **kwargs)

    if not data_frames:
        print("ERROR: no data loaded. Check tickers and network.", file=sys.stderr)
        return 1
    for ticker, df in data_frames.items():
        print(f"  {describe(df, ticker)}")

    # -- 2. Splits -----------------------------------------------------
    print("\n[2/6] Building train/test splits...")
    # Every ticker must share the same train/test boundary, which means they
    # must share a common date index. Intersecting ALL of them is the naive
    # move and it is badly wrong at scale: across 848 constituents the window
    # would begin at the most recent IPO in the index, collapsing a 14-year
    # study into a few months.
    #
    # Instead, require a minimum history and drop anything shorter. That trades
    # one bias for a smaller, statable one: the study covers companies listed
    # for at least `--min-history-years`, so recent listings are excluded. That
    # is a form of survivorship bias, it is disclosed in the report, and it is
    # far less damaging than silently truncating the sample period.
    cutoff = pd.Timestamp.today() - pd.DateOffset(years=args.min_history_years)
    long_enough = {t: df for t, df in data_frames.items() if df.index[0] <= cutoff}
    dropped = len(data_frames) - len(long_enough)

    if len(long_enough) < 5:
        print(f"ERROR: only {len(long_enough)} tickers have "
              f"{args.min_history_years}y of history.", file=sys.stderr)
        return 1

    if dropped:
        print(f"  dropped {dropped} ticker(s) with under "
              f"{args.min_history_years}y of history")
    data_frames = long_enough

    common_index = sorted(set.intersection(*[set(df.index) for df in data_frames.values()]))
    common_index = pd.DatetimeIndex(common_index)
    print(f"  common window: {common_index[0].date()} to {common_index[-1].date()} "
          f"({len(common_index):,} shared bars across {len(data_frames)} tickers)")
    split = simple_split(common_index)
    print(f"  {split}")

    windows = walk_forward_windows(common_index)
    print(f"  {len(windows)} walk-forward window(s):")
    print(describe_splits(windows[:3]))
    if len(windows) > 3:
        print(f"  ... and {len(windows) - 3} more")

    # -- 3. Benchmark --------------------------------------------------
    print("\n[3/6] Benchmark (buy & hold)...")
    bench_gross = _equal_weight([price_returns(df).rename(t) for t, df in data_frames.items()])
    bench_result = {t: buy_and_hold(df, t, costs) for t, df in data_frames.items()}
    bench_net = _equal_weight([r.net_returns.rename(t) for t, r in bench_result.items()])
    bench_pos = _equal_weight([r.positions.rename(t) for t, r in bench_result.items()])
    bench_scores = score_on_split(
        bench_gross, bench_net, bench_pos, split,
        extra={"num_trades": sum(r.metrics_net["num_trades"] for r in bench_result.values()),
               "cost_paid": float(np.mean([r.total_cost_paid for r in bench_result.values()]))},
    )
    print(f"  buy & hold OOS Sharpe (net): {bench_scores['out_sample_net']:.2f}")

    # -- 4. Indicators -------------------------------------------------
    print("\n[4/6] Running indicators...")
    scores, equity, walk_fwd = {}, {"HOLD": (1 + bench_net.fillna(0)).cumprod()}, {}

    for name, (fn, _desc) in INDICATORS.items():
        gross, net, positions, extra = run_indicator(data_frames, name, fn, costs)
        scores[name] = score_on_split(gross, net, positions, split, extra=extra)
        walk_fwd[name] = run_walk_forward(gross, net, windows)
        equity[name] = (1 + net.fillna(0)).cumprod()

        s = scores[name]
        print(f"  {name:6} in-sample {s['in_sample_gross']:6.2f}  ->  "
              f"OOS gross {s['out_sample_gross']:6.2f}  ->  "
              f"OOS net {s['out_sample_net']:6.2f}   "
              f"({int(s['num_trades']):,} trades)")

    # -- 5. Charts -----------------------------------------------------
    print("\n[5/6] Drawing charts...")
    period_label = f"{common_index[0].date()} to {common_index[-1].date()}"
    universe_label = charts.summarise_universe(data_frames.keys())
    subtitle = f"{universe_label} | {period_label} | {costs.total_bps_per_side:.0f}bps per side"

    # The equity curves span every ticker's own history, which starts earlier
    # than the common index (that one is clipped to the LATEST first date, so
    # that splits are identical across tickers). Label each chart with the
    # span it actually shows rather than reusing one period string for both.
    equity_index = equity["HOLD"].index
    equity_subtitle = (f"{universe_label} | {equity_index[0].date()} to "
                       f"{equity_index[-1].date()} | equal-weighted, "
                       f"{costs.total_bps_per_side:.0f}bps per side")

    # Chart filenames carry the week number. Without this, every run
    # overwrites the previous week's images and the published archive
    # silently starts showing the wrong charts against old reports.
    tag = f"_week{args.week:02d}" if args.week else f"_{dt.date.today().isoformat()}"

    chart_paths = {
        "decay": render_themed(
            charts.decay_curve, f"decay_curve{tag}",
            scores=scores, benchmark_sharpe=bench_scores["out_sample_net"],
            subtitle=subtitle),
        "equity": render_themed(
            charts.equity_curves, f"equity_curves{tag}",
            curves=equity, subtitle=equity_subtitle),
    }
    print(f"  {chart_paths['decay'].name}")
    print(f"  {chart_paths['equity'].name}")

    if not args.skip_sensitivity:
        levels = [0.0, 2.0, 5.0, 10.0, 20.0, 40.0]
        sens = run_cost_sensitivity(data_frames, split, levels)
        # This chart varies the cost assumption, so quoting a single bps
        # figure in its subtitle would contradict the chart itself.
        sens_subtitle = (f"{universe_label} | out-of-sample window "
                         f"{split.test_start.date()} to {split.test_end.date()}")
        chart_paths["sensitivity"] = render_themed(
            charts.cost_sensitivity, f"cost_sensitivity{tag}",
            sensitivity=sens, cost_levels_bps=levels, subtitle=sens_subtitle)
        print(f"  {chart_paths['sensitivity'].name}")

    # -- 6. Report -----------------------------------------------------
    print("\n[6/6] Writing report...")
    descriptions = {name: desc for name, (_fn, desc) in INDICATORS.items()}
    markdown = weekly.build_report(
        scores=scores,
        descriptions=descriptions,
        benchmark=bench_scores,
        costs=costs,
        universe=list(data_frames.keys()),
        period_label=period_label,
        split_label=f"{split.label}, plus {len(windows)} walk-forward windows",
        charts=chart_paths,
        walk_forward=walk_fwd,
        week_number=args.week,
    )
    path = weekly.write_report(markdown, week_number=args.week)

    # Structured sidecar: the website builds from this, not from the markdown.
    payload = data.build_payload(
        scores=scores,
        descriptions=descriptions,
        benchmark=bench_scores,
        costs=costs,
        universe=list(data_frames.keys()),
        period_start=str(common_index[0].date()),
        period_end=str(common_index[-1].date()),
        split_label=f"{split.label}, plus {len(windows)} walk-forward windows",
        test_start=str(split.test_start.date()),
        test_end=str(split.test_end.date()),
        walk_forward=walk_fwd,
        charts=chart_paths,
        week_number=args.week,
        markdown_file=path.name,
    )
    json_path = data.write_payload(payload)

    print(f"\nDone. Report: {path}")
    print(f"Data:        {json_path}")
    print(f"Charts:      {REPORT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
