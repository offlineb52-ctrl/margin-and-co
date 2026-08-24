"""
VWAP -- Volume-Weighted Average Price.

READ THIS BEFORE QUOTING ANY VWAP RESULT
-----------------------------------------
True VWAP is an INTRADAY measure. It accumulates price x volume from the
opening bell and resets to zero at the start of every session. Institutional
desks use it as an execution benchmark: "did I buy below the day's VWAP?"

On DAILY bars, that quantity does not exist -- there is only one price-volume
observation per day, so a daily "VWAP" would just be the day's own typical
price, which tells you nothing.

So what this module computes is a ROLLING N-day volume-weighted average price:

    rolling VWAP = sum(typical_price * volume, N) / sum(volume, N)

That is a legitimate and useful indicator -- it is a moving average that
weights high-volume days more heavily, on the theory that price levels where
a lot of stock changed hands matter more than quiet drift. But it is NOT the
VWAP a trading desk means, and reporting it as "VWAP" without this caveat is
the kind of imprecision that gets picked apart in an interview.

The honest framing for the weekly report: "the retail community's daily-chart
VWAP is a rolling volume-weighted moving average; here is whether it beats an
ordinary moving average, and whether either survives costs."
"""

from __future__ import annotations

import pandas as pd

from indicators.base import LONG, SHORT, typical_price, validate_ohlcv


def rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Rolling N-day volume-weighted average price. See module caveat."""
    if window < 1:
        raise ValueError("window must be >= 1")

    tp = typical_price(df)
    volume = df["volume"].astype(float)

    pv = (tp * volume).rolling(window, min_periods=window).sum()
    vol = volume.rolling(window, min_periods=window).sum()

    # Guard against a fully halted stretch: zero traded volume means the
    # weighted average is undefined, not zero.
    return (pv / vol.where(vol > 0)).rename("vwap")


def vwap_signal(df: pd.DataFrame, window: int = 20) -> pd.Series:
    """Target position from price versus rolling VWAP.

    Long when the close is above the volume-weighted average (price is
    trading above where the volume has been), short when below. This is a
    trend-following rule, so expect it to behave more like EMA than like RSI.
    """
    validate_ohlcv(df)

    vwap_line = rolling_vwap(df, window)
    close = df["close"]

    position = pd.Series(0.0, index=df.index, dtype=float)
    position[close > vwap_line] = LONG
    position[close < vwap_line] = SHORT
    position[vwap_line.isna()] = 0.0
    return position.rename("position")


PARAM_GRID = {"window": [10, 20, 50]}
