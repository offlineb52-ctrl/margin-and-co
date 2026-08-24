"""
Which strategy the live portfolio trades, and why that one.

Kept in its own file so the choice is explicit and dated. If the traded
strategy ever changes, the honest move is to close this portfolio and open a
second one alongside it -- not to swap the rule underneath an existing track
record and carry the old equity curve forward.
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

from indicators.rsi import rsi, rsi_signal

# --------------------------------------------------------------------------
# The traded strategy
# --------------------------------------------------------------------------

STRATEGY_NAME = "RSI(14) mean reversion, long only"

RATIONALE = (
    "Across 763 S&P 500 and FTSE 350 companies, RSI was the only one of the "
    "four indicators to post a positive out-of-sample Sharpe ratio after costs "
    "(0.48). The other three are trend-following, and all three went negative "
    "net of what it cost to trade them. RSI still lost to buy & hold, which is "
    "why this portfolio publishes both side by side. It is being traded live "
    "because a rule that survived one honest test deserves a second one that "
    "cannot be re-run."
)

RULES = [
    "Buy when 14-day RSI closes below 30 (oversold).",
    "Hold until RSI recovers through 50, then sell.",
    "Never short. The research tests long/short; this book trades the long leg "
    "only, because stock borrow costs are real and this project does not model them.",
    "Equal weight across every live signal, capped at 20% of the book in any one name.",
    "Orders are decided on the close and filled at the next session's open.",
]

# A fixed, liquid, single-currency universe. US only: FTSE names are quoted in
# pence, and mixing currencies without an FX model would make the NAV wrong.
UNIVERSE: List[str] = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "JPM", "V",
    "UNH", "XOM", "JNJ", "PG", "HD", "CVX", "KO", "PEP",
    "COST", "WMT", "MCD", "CSCO",
]

STARTING_CAPITAL = 100_000.0
CURRENCY = "USD"
MAX_WEIGHT = 0.20
RSI_PERIOD = 14
OVERSOLD = 30.0
EXIT_LEVEL = 50.0


def signals_for(frames: Dict[str, pd.DataFrame], as_of: pd.Timestamp) -> Dict[str, Dict]:
    """The strategy's view of every ticker as at the close of `as_of`.

    Returns a dict per ticker with the target position (1 = long, 0 = flat),
    the RSI reading behind it, and a sentence explaining the decision -- the
    sentence is what gets published next to the trade, so a reader never has to
    take a fill on trust.
    """
    out: Dict[str, Dict] = {}

    for ticker, df in frames.items():
        window = df.loc[:as_of]
        if len(window) < RSI_PERIOD * 3:
            continue

        # Long/short signal from the research code, then the long leg only.
        full = rsi_signal(window, period=RSI_PERIOD,
                          oversold=OVERSOLD, overbought=100.0, exit_level=EXIT_LEVEL)
        position = 1.0 if full.iloc[-1] > 0 else 0.0
        value = float(rsi(window["close"], RSI_PERIOD).iloc[-1])

        if position > 0:
            reason = (f"RSI {value:.1f} — below {OVERSOLD:.0f} or holding since it was; "
                      f"waiting for a recovery through {EXIT_LEVEL:.0f}")
        else:
            reason = f"RSI {value:.1f} — no oversold signal"

        out[ticker] = {"position": position, "rsi": round(value, 2), "reason": reason}

    return out
