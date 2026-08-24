"""
MACD -- Moving Average Convergence Divergence.

Three lines:
    MACD line   = EMA(12) - EMA(26)          "is short-term momentum above long?"
    Signal line = EMA(9) of the MACD line    "is that momentum itself rising?"
    Histogram   = MACD - Signal              the gap between them

The standard rule is to go long when the MACD line crosses above the signal
line and short when it crosses below. Note that this is a smoothed version of
an EMA crossover -- MACD is a derivative of the same information, which is
why its results tend to correlate with the EMA strategy. If both "work", that
is one finding, not two.
"""

from __future__ import annotations

import pandas as pd

from indicators.base import crossover_position, validate_ohlcv
from indicators.ema import ema


def macd_lines(
    close: pd.Series,
    fast_span: int = 12,
    slow_span: int = 26,
    signal_span: int = 9,
) -> pd.DataFrame:
    """Return the MACD line, signal line, and histogram as three columns."""
    macd_line = ema(close, fast_span) - ema(close, slow_span)
    signal_line = ema(macd_line, signal_span)
    return pd.DataFrame({
        "macd": macd_line,
        "signal": signal_line,
        "histogram": macd_line - signal_line,
    })


def macd_signal(
    df: pd.DataFrame,
    fast_span: int = 12,
    slow_span: int = 26,
    signal_span: int = 9,
) -> pd.Series:
    """Target position from the MACD/signal-line crossover."""
    validate_ohlcv(df)

    lines = macd_lines(df["close"], fast_span, slow_span, signal_span)
    position = crossover_position(lines["macd"], lines["signal"])

    # Warm-up: the signal line is an EMA of an EMA difference, so it needs
    # roughly slow_span + signal_span bars before it means anything.
    position.iloc[: slow_span + signal_span] = 0.0
    return position


PARAM_GRID = {"fast_span": [8, 12], "slow_span": [21, 26], "signal_span": [5, 9]}
