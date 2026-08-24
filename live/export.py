"""
Export the live book as JSON for the website.

Same split of responsibilities as the research side: the ledger owns the
truth, this module only derives presentation figures from it and never writes
back. Anything shown on the public page has to be computable from the ledger
alone, so a reader with the ledger file can reproduce every number.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from live import ledger, strategy

OUT_FILE = ledger.STATE_DIR / "live.json"


def _round(value: Optional[float], places: int = 2) -> Optional[float]:
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    return round(value, places)


def closed_trades() -> List[Dict[str, Any]]:
    """Pair fills into completed round trips, FIFO, with realised P&L.

    A "trade" here is a full position: opened when the book goes from flat to
    long in a name, closed when it returns to flat. Rebalancing fills in
    between are folded into the same trade rather than counted separately --
    otherwise trimming a winner would show up as its own profitable trade and
    inflate the win rate.
    """
    open_lots: Dict[str, Dict[str, Any]] = {}
    trades: List[Dict[str, Any]] = []

    for fill in ledger.load_fills():
        ticker = fill["ticker"]
        shares = fill["shares"]
        gross = shares * fill["price"]

        if fill["side"] == "BUY":
            lot = open_lots.setdefault(ticker, {
                "ticker": ticker, "opened_on": fill["filled_on"],
                "shares": 0.0, "cost": 0.0, "fees": 0.0,
                "proceeds": 0.0, "sold": 0.0,
                "open_reason": fill.get("reason", ""),
                "backfilled": fill.get("backfilled", False),
            })
            lot["shares"] += shares
            lot["cost"] += gross
            lot["fees"] += fill["cost"]
            continue

        lot = open_lots.get(ticker)
        if not lot:
            continue

        lot["proceeds"] += gross
        lot["fees"] += fill["cost"]
        lot["sold"] += shares

        if lot["sold"] >= lot["shares"] - 1e-9:
            realised = lot["proceeds"] - lot["cost"] - lot["fees"]
            basis = lot["cost"] or 1.0
            trades.append({
                "ticker": ticker,
                "opened_on": lot["opened_on"],
                "closed_on": fill["filled_on"],
                "shares": int(lot["shares"]),
                "avg_entry": _round(lot["cost"] / lot["shares"]) if lot["shares"] else None,
                "avg_exit": _round(lot["proceeds"] / lot["sold"]) if lot["sold"] else None,
                "fees": _round(lot["fees"]),
                "pnl": _round(realised),
                "pnl_pct": _round(realised / basis, 4),
                "open_reason": lot["open_reason"],
                "close_reason": fill.get("reason", ""),
                "backfilled": lot["backfilled"] or fill.get("backfilled", False),
            })
            open_lots.pop(ticker, None)

    trades.sort(key=lambda t: t["closed_on"], reverse=True)
    return trades


def _max_drawdown(navs: List[float]) -> Optional[float]:
    if not navs:
        return None
    peak, worst = navs[0], 0.0
    for nav in navs:
        peak = max(peak, nav)
        worst = min(worst, nav / peak - 1.0)
    return _round(worst, 4)


def build_payload() -> Dict[str, Any]:
    meta = ledger.load_meta()
    if not meta:
        raise RuntimeError("no live portfolio; run live/run_live.py --backfill-from DATE")

    equity = ledger.load_equity()
    fills = ledger.load_fills()
    positions = ledger.current_positions()
    pending = ledger.load_pending()
    trades = closed_trades()

    capital = meta["starting_capital"]
    bench_file = ledger.STATE_DIR / "benchmark.json"
    benchmark = json.loads(bench_file.read_text()) if bench_file.exists() else {}

    navs = [s["nav"] for s in equity]
    last = equity[-1] if equity else {}
    last_date = last.get("date")
    bench_nav = benchmark.get(last_date)

    # Latest close for each holding, so open P&L is marked, not assumed.
    latest_prices: Dict[str, float] = {}
    for fill in reversed(fills):
        latest_prices.setdefault(fill["ticker"], fill["price"])

    holdings = []
    for ticker, position in sorted(positions.items()):
        basis = position["cost_basis"]
        holdings.append({
            "ticker": ticker,
            "shares": int(position["shares"]),
            "cost_basis": _round(basis),
            "opened_on": position["opened_on"],
            "value_at_cost": _round(position["shares"] * basis),
        })

    wins = [t for t in trades if (t["pnl"] or 0) > 0]

    payload = {
        "schema_version": 1,
        "generated": dt.date.today().isoformat(),
        "strategy": {
            "name": meta["strategy"],
            # Read live from the strategy module, not from frozen meta. The
            # terms of the book (capital, universe, costs) are fixed at
            # inception and must never move; the explanation of WHY the
            # strategy was chosen is prose, and it should stay current as
            # later research refines the reasoning.
            "rationale": strategy.RATIONALE,
            "rules": strategy.RULES,
            "universe": meta["universe"],
            "universe_size": len(meta["universe"]),
            "costs_bps_per_side": meta["costs_bps_per_side"],
            "max_weight": strategy.MAX_WEIGHT,
        },
        "book": {
            "opened": meta.get("opened", meta.get("started")),
            "inception": meta.get("inception", meta.get("started")),
            "currency": meta["currency"],
            "starting_capital": capital,
            "sessions": len(equity),
            "last_mark": last_date,
        },
        "performance": {
            "nav": last.get("nav"),
            "cash": last.get("cash"),
            "market_value": last.get("market_value"),
            "total_return": _round(last.get("nav", capital) / capital - 1, 4),
            "benchmark_nav": bench_nav,
            "benchmark_return": _round(bench_nav / capital - 1, 4) if bench_nav else None,
            "excess_return": _round((last.get("nav", capital) - bench_nav) / capital, 4)
                              if bench_nav else None,
            "max_drawdown": _max_drawdown(navs),
            "avg_invested": _round(sum(s["invested_pct"] for s in equity) / len(equity), 4)
                             if equity else None,
            "current_invested": last.get("invested_pct"),
            "total_costs_paid": _round(sum(f["cost"] for f in fills)),
            "closed_trades": len(trades),
            "win_rate": _round(len(wins) / len(trades), 4) if trades else None,
        },
        "positions": holdings,
        "pending_orders": pending,
        "closed_trades_list": trades[:40],
        "fills": list(reversed(fills))[:60],
        "equity_curve": [
            {"date": s["date"], "nav": s["nav"],
             "benchmark": benchmark.get(s["date"]),
             "invested": s["invested_pct"], "backfilled": s.get("backfilled", False)}
            for s in equity
        ],
        "live_since": next((s["date"] for s in equity if not s.get("backfilled")), None),
        "backfilled_sessions": sum(1 for s in equity if s.get("backfilled")),
    }
    return payload


def write(outfile: Optional[Path] = None) -> Path:
    outfile = outfile or OUT_FILE
    outfile.write_text(json.dumps(build_payload(), indent=2), encoding="utf-8")
    return outfile


def render_charts(payload: Optional[Dict[str, Any]] = None) -> List[Path]:
    """Draw the live equity chart in both themes.

    Imported lazily so that `write()` -- which the daily job calls -- has no
    matplotlib dependency and stays fast.
    """
    from config import REPORT_DIR
    from reports import charts

    payload = payload or build_payload()
    curve = payload["equity_curve"]
    subtitle = (f"{payload['strategy']['universe_size']} US large caps | "
                f"since {payload['book']['inception']} | "
                f"{payload['strategy']['costs_bps_per_side']:.0f}bps per side")

    written = []
    for dark in (False, True):
        name = "live_equity_dark.png" if dark else "live_equity.png"
        written.append(charts.live_equity(
            dates=[c["date"] for c in curve],
            nav=[c["nav"] for c in curve],
            benchmark=[c["benchmark"] for c in curve],
            exposure=[c["invested"] for c in curve],
            live_from=payload["live_since"],
            starting_capital=payload["book"]["starting_capital"],
            subtitle=subtitle,
            outfile=REPORT_DIR / name,
            dark=dark,
        ))
    return written


if __name__ == "__main__":
    path = write()
    render_charts()
    data = json.loads(path.read_text())
    p = data["performance"]
    print(f"Wrote {path}")
    print(f"  NAV {p['nav']:,.2f}  return {p['total_return']:+.2%}  "
          f"vs benchmark {p['benchmark_return']:+.2%}  excess {p['excess_return']:+.2%}")
    print(f"  {data['book']['sessions']} sessions, {p['closed_trades']} closed trades, "
          f"win rate {p['win_rate']:.0%}, avg invested {p['avg_invested']:.0%}")
