"""
The paper broker.

Simulates execution honestly rather than optimistically. Three rules govern
every fill, and each exists because the opposite is the standard way people
flatter a paper track record:

  1. **Orders fill at the NEXT session's open, never at the close that
     generated them.** You cannot decide from a closing price and also trade
     at it. The gap between the two -- the overnight move -- is real risk that
     a same-close fill quietly deletes.

  2. **Every fill pays the full cost model**: half spread, commission and
     slippage, on the traded notional. Costs are charged to cash, not netted
     out of the reported return, so they show up in the ledger as real money.

  3. **Whole shares only, and no leverage.** If the cash is not there, the
     order is cut down or skipped. A paper portfolio that can buy 1,000.37
     shares on margin it does not have is not modelling anything.

WHY THE LIVE PORTFOLIO IS LONG-ONLY
------------------------------------
The research tests each indicator long/short, because that is how the rule is
defined. The live portfolio trades the long leg only. Shorting incurs a stock
borrow fee that varies by name and by day, and this project does not model it
-- so a live short book would report a return that no one could actually have
earned. Excluding shorts costs some fidelity to the tested rule and buys back
something worth more: every number in the track record is one you could have
actually banked.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional

from config import CostModel


@dataclass(frozen=True)
class Order:
    """An intention recorded on one day, to be executed on the next."""

    decided_on: str
    ticker: str
    side: str          # BUY or SELL
    shares: int
    reason: str        # the signal that caused it, in plain words
    signal_value: Optional[float] = None

    def to_dict(self) -> Dict:
        return {
            "decided_on": self.decided_on,
            "ticker": self.ticker,
            "side": self.side,
            "shares": self.shares,
            "reason": self.reason,
            "signal_value": self.signal_value,
        }


def target_weights(
    signals: Dict[str, float],
    max_weight: float = 0.20,
) -> Dict[str, float]:
    """Equal weight across every name the strategy currently wants to be long.

    Equal weighting is crude, and deliberately so: any cleverer scheme
    (volatility targeting, conviction sizing) would be another set of
    parameters chosen with hindsight, and the point of this portfolio is to
    test the signal, not the sizing.

    `max_weight` caps concentration so that a week with one live signal does
    not put the whole book in a single stock.
    """
    longs = [t for t, v in signals.items() if v > 0]
    if not longs:
        return {}

    weight = min(1.0 / len(longs), max_weight)
    return {t: weight for t in longs}


def plan_orders(
    decided_on: str,
    target: Dict[str, float],
    holdings: Dict[str, Dict[str, float]],
    prices: Dict[str, float],
    nav: float,
    reasons: Dict[str, str],
    signal_values: Optional[Dict[str, float]] = None,
    min_trade_value: float = 250.0,
    rebalance_band: float = 0.25,
) -> List[Order]:
    """Work out the trades needed to move from `holdings` towards `target`.

    Entries and exits always trade. Everything in between is governed by a
    tolerance band: an existing position is only rebalanced once it has
    drifted more than `rebalance_band` away from its target weight.

    This matters more than it looks. Without a band, a 20% position in a
    $100k book drifts past a $250 minimum on any 1.2% daily move, so the
    portfolio rebalances almost every session and pays the spread each time.
    The first version of this file did exactly that: 118 fills to hold an
    average of two positions. Which is, in miniature, precisely the failure
    this whole project documents -- so it would be a poor look to commit it
    in the live book.
    """
    signal_values = signal_values or {}
    orders: List[Order] = []

    tickers = sorted(set(target) | set(holdings))

    for ticker in tickers:
        price = prices.get(ticker)
        if not price or price <= 0:
            continue

        held = holdings.get(ticker, {}).get("shares", 0.0)
        wanted_value = target.get(ticker, 0.0) * nav
        wanted_shares = math.floor(wanted_value / price)

        delta = wanted_shares - held
        if abs(delta) * price < min_trade_value:
            continue

        opening = held == 0 and wanted_shares > 0
        closing = wanted_shares == 0 and held != 0

        # A position already on the books only moves once it has drifted
        # meaningfully; otherwise the spread is paid for nothing.
        if not opening and not closing:
            drift = abs(held * price - wanted_value) / max(wanted_value, 1.0)
            if drift < rebalance_band:
                continue

        side = "BUY" if delta > 0 else "SELL"

        # The reason is published next to the trade, so it has to describe
        # THIS action, not merely the ticker's current state.
        signal_note = reasons.get(ticker, "")
        if opening:
            reason = f"Opening position — {signal_note}"
        elif closing:
            reason = f"Closing position — {signal_note}"
        elif side == "BUY":
            reason = f"Topping up to target weight — {signal_note}"
        else:
            reason = f"Trimming back to target weight — {signal_note}"
        orders.append(Order(
            decided_on=decided_on,
            ticker=ticker,
            side=side,
            shares=int(abs(delta)),
            reason=reason,
            signal_value=signal_values.get(ticker),
        ))

    # Sells first: they release the cash the buys need.
    orders.sort(key=lambda o: 0 if o.side == "SELL" else 1)
    return orders


def execute(
    orders: List[Dict],
    open_prices: Dict[str, float],
    fill_date: str,
    cash: float,
    costs: CostModel,
) -> List[Dict]:
    """Fill pending orders at the given session's open, charging full costs.

    Returns the fills to be appended to the ledger. An order whose ticker has
    no opening price that day -- a halt, a holiday, a delisting -- is dropped
    rather than filled at a stale price.
    """
    rate = costs.total_cost_per_unit_turnover
    fills: List[Dict] = []
    available = cash

    for order in orders:
        ticker = order["ticker"]
        price = open_prices.get(ticker)
        if not price or price <= 0:
            continue

        shares = int(order["shares"])
        if shares <= 0:
            continue

        if order["side"] == "BUY":
            # Trim the order to what the cash can actually pay for, including
            # the cost of trading it.
            affordable = math.floor(available / (price * (1 + rate)))
            shares = min(shares, max(affordable, 0))
            if shares <= 0:
                continue

        gross = shares * price
        cost = gross * rate

        available += (-gross - cost) if order["side"] == "BUY" else (gross - cost)

        fills.append({
            "decided_on": order["decided_on"],
            "filled_on": fill_date,
            "ticker": ticker,
            "side": order["side"],
            "shares": shares,
            "price": round(price, 4),
            "gross": round(gross, 2),
            "cost": round(cost, 2),
            "reason": order.get("reason", ""),
            "signal_value": order.get("signal_value"),
        })

    return fills


def mark_to_market(
    holdings: Dict[str, Dict[str, float]],
    close_prices: Dict[str, float],
    cash: float,
) -> Dict[str, float]:
    """Value the book at the close. Positions with no price keep their basis."""
    market_value = 0.0
    priced, stale = 0, 0

    for ticker, position in holdings.items():
        price = close_prices.get(ticker)
        if price and price > 0:
            market_value += position["shares"] * price
            priced += 1
        else:
            market_value += position["shares"] * position["cost_basis"]
            stale += 1

    return {
        "cash": round(cash, 2),
        "market_value": round(market_value, 2),
        "nav": round(cash + market_value, 2),
        "positions_priced": priced,
        "positions_stale": stale,
    }
