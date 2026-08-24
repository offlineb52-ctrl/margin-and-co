"""
Shared conventions for every indicator in this project.

THE SIGNAL CONTRACT
-------------------
Every indicator function takes a DataFrame of OHLCV bars and returns a pandas
Series of TARGET POSITIONS, aligned to the same index:

    +1  = fully long
     0  = flat (in cash)
    -1  = fully short

The position on date t is the position the indicator wants to hold based on
information available at the CLOSE of date t.

Turning that into a return is the backtester's job, not the indicator's, and
the backtester shifts the series forward by one bar before applying it. That
one-line shift is the difference between an honest backtest and a time
machine: without it, you earn day t's return using day t's closing price,
which you could not have known until the day was over. Keeping the shift in
exactly one place means it cannot be forgotten in one strategy and applied in
another.

WHY POSITIONS AND NOT "BUY/SELL EVENTS"
---------------------------------------
A target-position series makes turnover trivial to compute (|change| in
position), and turnover is what you pay costs on. Event-based signals require
tracking state to know whether a "buy" is opening or adding, which is where
most homemade backtesters develop quiet bugs.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LONG, FLAT, SHORT = 1.0, 0.0, -1.0


def validate_ohlcv(df: pd.DataFrame) -> None:
    """Fail loudly and early if the frame is not what an indicator expects."""
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {sorted(missing)}")
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame index must be a DatetimeIndex")
    if not df.index.is_monotonic_increasing:
        raise ValueError("DataFrame index must be sorted ascending by date")


def typical_price(df: pd.DataFrame) -> pd.Series:
    """(high + low + close) / 3 -- the standard proxy for 'where it traded'.

    Used by VWAP. Using the close alone would ignore the range the stock
    actually traded through during the session.
    """
    return (df["high"] + df["low"] + df["close"]) / 3.0


def hold_between_thresholds(
    series: pd.Series,
    entry_low: float,
    entry_high: float,
    exit_level: float,
) -> pd.Series:
    """Turn an oscillator into a persistent position, the way a trader does.

    A retail trader does not hold a position only on the days RSI is below 30
    -- they buy when it drops below 30 and hold until it recovers past 50.
    This helper encodes exactly that behaviour:

        cross below `entry_low`   -> go long
        cross above `entry_high`  -> go short
        cross back past `exit_level` -> flatten

    Implemented as a forward-fill over an event series, which is vectorised
    and, more importantly, easy to read six months from now.
    """
    position = pd.Series(np.nan, index=series.index, dtype=float)

    position[series < entry_low] = LONG
    position[series > entry_high] = SHORT

    # Once the oscillator returns to the middle, the trade thesis is done.
    # We only flatten a long from above and a short from below, so the exit
    # rule cannot accidentally re-open a position.
    back_to_middle = (series >= exit_level) & (series <= entry_high)
    position[back_to_middle & (series.shift(1) < exit_level)] = FLAT

    position = position.ffill().fillna(FLAT)
    return position.rename("position")


def crossover_position(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """+1 while `fast` is above `slow`, -1 while below. The classic crossover.

    Note this is always in the market -- it is never flat. That matters for
    costs: an always-on strategy still only pays when it FLIPS, so turnover is
    driven by how often the two lines cross, not by how long they stay apart.
    """
    position = pd.Series(FLAT, index=fast.index, dtype=float)
    position[fast > slow] = LONG
    position[fast < slow] = SHORT

    # Before both series have enough history, hold no position at all.
    warmup = fast.isna() | slow.isna()
    position[warmup] = FLAT
    return position.rename("position")
