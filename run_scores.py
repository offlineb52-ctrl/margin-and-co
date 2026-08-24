"""
Score a week and publish both tiers.

    python run_scores.py --week 2                    # AAPL pilot
    python run_scores.py --week 2 --universe sample  # the 15-name set
    python run_scores.py --week 2 --overwrite        # re-run a recorded week

Runs the Survival Score over every indicator/ticker pair, writes the results to
the SQLite archive, and generates the free and Pro reports. The two reports are
built by separate functions from separate data, so the public one cannot leak
members-only content.
"""

from __future__ import annotations

import argparse
import sys
from typing import List

import archive
import scoring
from config import DEFAULT_COSTS, FTSE350_SAMPLE, SMOKE_TEST_TICKERS, SP500_SAMPLE
from data import universe as universe_lists
from data.loader import load_universe
from indicators import INDICATORS
from reports import tiers

UNIVERSES = {
    "smoke": lambda: SMOKE_TEST_TICKERS,
    "sample": lambda: SP500_SAMPLE + FTSE350_SAMPLE,
    "sp500": lambda: universe_lists.load("sp500"),
    "all": lambda: universe_lists.load("sp500") + universe_lists.load("ftse350"),
}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Survival Score for one week")
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--universe", choices=sorted(UNIVERSES), default="smoke")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace an already-recorded week (changes history)")
    parser.add_argument("--notes", default="")
    args = parser.parse_args(argv)

    tickers = UNIVERSES[args.universe]()
    print(f"[1/4] Loading {len(tickers)} ticker(s)...")
    frames = load_universe(tickers, verbose=False)
    if not frames:
        print("ERROR: no data. Run warm_cache.py first.", file=sys.stderr)
        return 1
    print(f"      {len(frames)} loaded")

    print(f"\n[2/4] Scoring {len(frames)} x {len(INDICATORS)} combinations...")
    scores = scoring.scoreboard(frames, INDICATORS, verbose=False)
    if scores.empty:
        print("ERROR: nothing scored.", file=sys.stderr)
        return 1

    top = scores.iloc[0]
    print(f"      best: {top['indicator']} on {top['ticker']} "
          f"at {top['score']:.1f} ({top['verdict']})")
    print(f"      median score {scores['score'].median():.1f}, "
          f"{int((scores['score'] >= 8).sum())} of {len(scores)} survived")

    print(f"\n[3/4] Recording week {args.week}...")
    try:
        rows = archive.record_week(
            scores, week=args.week, universe=list(frames),
            costs_bps=DEFAULT_COSTS.total_bps_per_side,
            notes=args.notes, overwrite=args.overwrite,
        )
        print(f"      {rows} rows -> {archive.DB_PATH.name}")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print("\n[4/4] Generating reports...")
    weeks = archive.weeks()
    charts = {"decay": f"decay_curve_week{args.week:02d}.png"}

    free = tiers.generate_free_report(
        scores, args.week, list(frames), DEFAULT_COSTS.total_bps_per_side,
        charts=charts, archive_weeks=weeks)
    pro = tiers.generate_pro_report(
        scores, args.week, list(frames), DEFAULT_COSTS.total_bps_per_side,
        charts=charts, archive_weeks=weeks,
        score_history=archive.trend(min_weeks=1))

    for report in (free, pro):
        for kind, path in tiers.write_report(report).items():
            print(f"      {report.tier:4} {kind}: {path.name}")

    print(f"\nHeadline: {free.headline}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
