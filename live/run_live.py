"""
The live paper-trading job.

Run once per trading day, after the close:

    python live/run_live.py                      # advance one day
    python live/run_live.py --backfill-from 2026-02-23   # open the book
    python live/run_live.py --status             # show the book, change nothing

WHAT ONE DAY LOOKS LIKE
-----------------------
  1. Fill yesterday's committed orders at today's OPEN, paying full costs.
  2. Read today's CLOSE and compute the strategy's signals from it.
  3. Write the orders that implies -- to be filled at tomorrow's open.
  4. Mark the book to market at today's close and append the snapshot.

Step 3 always happens before step 1 can act on it, on a later run. That
ordering is the entire integrity claim of this portfolio: nothing is ever
filled at a price that was known when the decision was made.

ON BACKFILLING
--------------
The book can be opened with history, using `--backfill-from`. Those days run
through the identical sequence above, so no future information touches any
decision -- but they were reconstructed on the day the book opened rather than
published in advance. Every such entry is flagged `backfilled: true` and the
website labels them separately. After the backfill, days accumulate one at a
time and can never be recomputed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import DEFAULT_COSTS  # noqa: E402
from data.loader import load_ticker  # noqa: E402
from live import broker, ledger, strategy  # noqa: E402


def load_frames(tickers: List[str], verbose: bool = True) -> Dict[str, pd.DataFrame]:
    frames = {}
    for ticker in tickers:
        try:
            frames[ticker] = load_ticker(ticker, verbose=False)
        except Exception as exc:  # noqa: BLE001
            if verbose:
                print(f"  [skip] {ticker}: {exc}")
    return frames


def trading_days(frames: Dict[str, pd.DataFrame], start: Optional[str] = None) -> pd.DatetimeIndex:
    """Dates on which at least half the universe printed a bar."""
    counts: Dict[pd.Timestamp, int] = {}
    for df in frames.values():
        for date in df.index:
            counts[date] = counts.get(date, 0) + 1

    threshold = max(len(frames) // 2, 1)
    days = sorted(d for d, n in counts.items() if n >= threshold)
    index = pd.DatetimeIndex(days)
    return index[index >= pd.Timestamp(start)] if start else index


def prices_on(frames: Dict[str, pd.DataFrame], date: pd.Timestamp, field: str) -> Dict[str, float]:
    out = {}
    for ticker, df in frames.items():
        if date in df.index:
            value = df.at[date, field]
            if pd.notna(value) and value > 0:
                out[ticker] = float(value)
    return out


def benchmark_nav(frames: Dict[str, pd.DataFrame], days: pd.DatetimeIndex,
                  capital: float, costs) -> Dict[str, float]:
    """Equal-weight buy & hold of the same universe, bought at the same open.

    The comparison the portfolio has to beat. It pays the same costs on its one
    round of purchases, so it is not being flattered either.
    """
    if len(days) == 0:
        return {}

    first_open = prices_on(frames, days[0], "open")
    if not first_open:
        return {}

    rate = costs.total_cost_per_unit_turnover
    per_name = capital / len(first_open)
    shares = {t: int(per_name / (p * (1 + rate))) for t, p in first_open.items()}

    spent = sum(shares[t] * p * (1 + rate) for t, p in first_open.items())
    cash = capital - spent

    navs = {}
    for day in days:
        closes = prices_on(frames, day, "close")
        value = sum(n * closes.get(t, first_open.get(t, 0.0)) for t, n in shares.items())
        navs[day.date().isoformat()] = round(cash + value, 2)
    return navs


def advance_one_day(frames, day: pd.Timestamp, costs, backfilled: bool) -> Dict:
    """Run the four steps for a single session. Returns the day's snapshot."""
    iso = day.date().isoformat()
    meta = ledger.load_meta()

    # --- 1. Fill what was committed yesterday, at today's open -----------
    pending = ledger.load_pending()
    if pending:
        opens = prices_on(frames, day, "open")
        cash = ledger.cash_balance(meta["starting_capital"])
        fills = broker.execute(pending, opens, iso, cash, costs)
        for fill in fills:
            fill["backfilled"] = backfilled
            ledger.append_fill(fill)
        ledger.set_pending([])

    # --- 2 & 3. Read today's close, commit tomorrow's orders -------------
    signals = strategy.signals_for(frames, day)
    positions = {t: s["position"] for t, s in signals.items()}
    reasons = {t: s["reason"] for t, s in signals.items()}
    rsi_values = {t: s["rsi"] for t, s in signals.items()}

    closes = prices_on(frames, day, "close")
    holdings = ledger.current_positions()
    cash = ledger.cash_balance(meta["starting_capital"])
    marks = broker.mark_to_market(holdings, closes, cash)

    weights = broker.target_weights(positions, max_weight=strategy.MAX_WEIGHT)
    orders = broker.plan_orders(
        decided_on=iso, target=weights, holdings=holdings, prices=closes,
        nav=marks["nav"], reasons=reasons, signal_values=rsi_values,
    )
    ledger.set_pending([o.to_dict() for o in orders])

    # --- 4. Snapshot ------------------------------------------------------
    invested = marks["market_value"] / marks["nav"] if marks["nav"] else 0.0
    snapshot = {
        "date": iso,
        "nav": marks["nav"],
        "cash": marks["cash"],
        "market_value": marks["market_value"],
        "positions": len(holdings),
        "invested_pct": round(invested, 4),
        "live_signals": int(sum(1 for v in positions.values() if v > 0)),
        "orders_committed": len(orders),
        "backfilled": backfilled,
    }
    ledger.append_snapshot(snapshot)
    return snapshot


def print_status() -> None:
    meta = ledger.load_meta()
    if not meta:
        print("No live portfolio yet. Open one with --backfill-from YYYY-MM-DD.")
        return

    equity = ledger.load_equity()
    fills = ledger.load_fills()
    holdings = ledger.current_positions()
    pending = ledger.load_pending()

    print(f"Strategy       {meta['strategy']}")
    print(f"Opened         {meta['opened']}  with {meta['currency']} {meta['starting_capital']:,.0f}")
    if meta.get("inception") and meta["inception"] != meta["opened"]:
        print(f"Inception      {meta['inception']}  (backfilled to this date)")
    if equity:
        last = equity[-1]
        ret = last["nav"] / meta["starting_capital"] - 1
        print(f"Last mark      {last['date']}  NAV {last['nav']:,.2f}  ({ret:+.2%})")
        print(f"               {last['positions']} positions, {last['invested_pct']:.0%} invested")
    print(f"Fills          {len(fills)}  ({sum(1 for f in fills if not f.get('backfilled')) } recorded live)")
    print(f"Pending orders {len(pending)}")
    for ticker, position in sorted(holdings.items()):
        print(f"   {ticker:6} {position['shares']:>6.0f} @ {position['cost_basis']:.2f}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Live paper trading job")
    parser.add_argument("--backfill-from", type=str, default=None,
                        help="open the book on this date and replay forward")
    parser.add_argument("--status", action="store_true", help="print the book, change nothing")
    parser.add_argument("--reset", action="store_true", help="DELETE all live state and start over")
    args = parser.parse_args(argv)

    if args.status:
        print_status()
        return 0

    if args.reset:
        confirm = "--backfill-from" in (argv or sys.argv)
        if not confirm:
            print("--reset must be combined with --backfill-from, so the book is "
                  "never left empty by accident.", file=sys.stderr)
            return 1
        ledger.reset()
        print("Live state cleared.")

    costs = DEFAULT_COSTS
    print(f"Loading {len(strategy.UNIVERSE)} tickers...")
    frames = load_frames(strategy.UNIVERSE)
    if not frames:
        print("ERROR: no price data. Run warm_cache.py first.", file=sys.stderr)
        return 1

    meta = ledger.init_meta(
        strategy=strategy.STRATEGY_NAME,
        rationale=strategy.RATIONALE,
        capital=strategy.STARTING_CAPITAL,
        universe=sorted(frames),
        costs_bps=costs.total_bps_per_side,
        currency=strategy.CURRENCY,
        inception=args.backfill_from,
    )

    done = {s["date"] for s in ledger.load_equity()}
    start = args.backfill_from or meta.get("inception") or meta["opened"]
    days = trading_days(frames, start)
    todo = [d for d in days if d.date().isoformat() not in done]

    if not todo:
        print("Already up to date.")
        print_status()
        return 0

    today = dt.date.today().isoformat()
    print(f"Advancing {len(todo)} session(s): "
          f"{todo[0].date()} -> {todo[-1].date()}\n")

    for day in todo:
        # Anything dated before today was reconstructed, not published ahead.
        backfilled = day.date().isoformat() < today
        snap = advance_one_day(frames, day, costs, backfilled)
        if len(todo) <= 15 or day == todo[-1] or snap["orders_committed"]:
            flag = "backfill" if backfilled else "LIVE"
            print(f"  {snap['date']}  NAV {snap['nav']:>11,.2f}  "
                  f"pos {snap['positions']:>2}  invested {snap['invested_pct']:>5.0%}  "
                  f"signals {snap['live_signals']:>2}  orders {snap['orders_committed']:>2}  [{flag}]")

    # Benchmark is recomputed each run; it holds no state of its own.
    navs = benchmark_nav(frames, days, meta["starting_capital"], costs)
    (ledger.STATE_DIR / "benchmark.json").write_text(
        pd.Series(navs).to_json(indent=2), encoding="utf-8")

    print()
    print_status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
