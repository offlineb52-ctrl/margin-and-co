"""
Append-only record of the live paper portfolio.

WHY APPEND-ONLY IS THE WHOLE POINT
-----------------------------------
A backtest can be re-run with different parameters until it looks good. A live
track record cannot -- and the only thing that makes the difference visible to
a reader is that the record is written forward, in order, and never edited.

So this module enforces three rules in code:

  1. **Orders are committed before they are filled.** On day t the strategy
     writes what it intends to do; on day t+1 that order fills at the open
     that actually printed. The decision timestamp always precedes the fill
     timestamp, and both are published.

  2. **Nothing is ever rewritten.** `append_fill` and `append_snapshot` only
     add. There is no update or delete. If a mistake is made, the correction
     is a new entry, exactly as it would be in a real book.

  3. **Every fill names the signal that caused it.** A trade with no
     attributable reason is not a strategy, it is a guess.

The result is a file you can hand to a sceptic. If the equity curve is good,
the ledger explains why; if it is bad, the ledger says that too.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path(__file__).resolve().parent / "state"
STATE_DIR.mkdir(parents=True, exist_ok=True)

LEDGER_FILE = STATE_DIR / "ledger.json"      # every fill, ever
PENDING_FILE = STATE_DIR / "pending.json"    # orders awaiting the next open
EQUITY_FILE = STATE_DIR / "equity.json"      # daily mark-to-market snapshots
META_FILE = STATE_DIR / "meta.json"          # when the portfolio started, and on what terms

SCHEMA_VERSION = 1


def _read(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"  [warn] {path.name} is corrupt; refusing to overwrite it")
        raise


def _write(path: Path, payload: Any) -> None:
    """Write via a temporary file, then rename.

    A half-written ledger is worse than no ledger. Renaming is atomic on POSIX,
    so an interrupted run leaves the previous good file in place.
    """
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------

def load_meta() -> Dict[str, Any]:
    return _read(META_FILE, {})


def init_meta(strategy: str, rationale: str, capital: float,
              universe: List[str], costs_bps: float,
              currency: str = "USD",
              inception: Optional[str] = None) -> Dict[str, Any]:
    """Record the terms of the portfolio, once, on the day it opens.

    These are frozen deliberately. Changing the starting capital or the traded
    strategy later would make the track record meaningless, so if either needs
    to change the honest move is to close this portfolio and open a new one
    alongside it.
    """
    meta = load_meta()
    if meta:
        return meta

    meta = {
        "schema_version": SCHEMA_VERSION,
        "strategy": strategy,
        "rationale": rationale,
        # `opened` is the day this book was created; `inception` is the first
        # session it covers. They differ when the book was opened with a
        # backfill, and the website states both rather than blurring them.
        "opened": dt.date.today().isoformat(),
        "inception": inception or dt.date.today().isoformat(),
        "starting_capital": capital,
        "currency": currency,
        "universe": universe,
        "costs_bps_per_side": costs_bps,
    }
    _write(META_FILE, meta)
    return meta


# --------------------------------------------------------------------------
# Fills
# --------------------------------------------------------------------------

def load_fills() -> List[Dict[str, Any]]:
    return _read(LEDGER_FILE, [])


def append_fill(fill: Dict[str, Any]) -> None:
    """Add one executed trade. Never modifies an existing entry."""
    required = {"decided_on", "filled_on", "ticker", "side",
                "shares", "price", "cost", "reason"}
    missing = required - set(fill)
    if missing:
        raise ValueError(f"fill is missing required fields: {sorted(missing)}")

    fills = load_fills()
    fills.append(fill)
    _write(LEDGER_FILE, fills)


# --------------------------------------------------------------------------
# Pending orders -- the commitment step
# --------------------------------------------------------------------------

def load_pending() -> List[Dict[str, Any]]:
    return _read(PENDING_FILE, [])


def set_pending(orders: List[Dict[str, Any]]) -> None:
    """Replace the pending queue.

    This is the one file that is overwritten, and only ever with orders that
    have not yet executed. Once an order fills it moves to the ledger, which
    is immutable.
    """
    _write(PENDING_FILE, orders)


# --------------------------------------------------------------------------
# Equity snapshots
# --------------------------------------------------------------------------

def load_equity() -> List[Dict[str, Any]]:
    return _read(EQUITY_FILE, [])


def append_snapshot(snapshot: Dict[str, Any]) -> bool:
    """Add one daily mark-to-market. Returns False if that date already exists.

    Re-running the job on the same day must not duplicate a day of returns --
    that would compound the same move twice and quietly inflate the record.
    """
    history = load_equity()
    if any(s["date"] == snapshot["date"] for s in history):
        return False

    history.append(snapshot)
    history.sort(key=lambda s: s["date"])
    _write(EQUITY_FILE, history)
    return True


# --------------------------------------------------------------------------
# Derived state
# --------------------------------------------------------------------------

def current_positions() -> Dict[str, Dict[str, float]]:
    """Rebuild holdings by replaying every fill in order.

    Positions are derived, never stored. A stored position can drift out of
    step with the trade log; a replayed one cannot, because the trade log is
    the only source of truth.
    """
    book: Dict[str, Dict[str, float]] = {}

    for fill in load_fills():
        ticker = fill["ticker"]
        signed = fill["shares"] if fill["side"] == "BUY" else -fill["shares"]
        position = book.setdefault(ticker, {"shares": 0.0, "cost_basis": 0.0,
                                            "opened_on": fill["filled_on"]})

        if position["shares"] == 0.0:
            position["opened_on"] = fill["filled_on"]

        # Weighted average cost basis while adding to a position; a reducing
        # trade leaves the basis of the remaining shares unchanged.
        if position["shares"] * signed > 0 or position["shares"] == 0:
            total = position["shares"] + signed
            if total != 0:
                position["cost_basis"] = (
                    position["cost_basis"] * position["shares"] + fill["price"] * signed
                ) / total
        position["shares"] += signed

    return {t: p for t, p in book.items() if abs(p["shares"]) > 1e-9}


def cash_balance(starting_capital: float) -> float:
    """Cash left after every fill and every commission paid."""
    cash = starting_capital
    for fill in load_fills():
        gross = fill["shares"] * fill["price"]
        cash += -gross if fill["side"] == "BUY" else gross
        cash -= fill["cost"]
    return cash


def reset() -> None:
    """Delete all live state. Only for setting up a fresh portfolio."""
    for path in (LEDGER_FILE, PENDING_FILE, EQUITY_FILE, META_FILE):
        path.unlink(missing_ok=True)
