"""
RSI -- Relative Strength Index (Wilder, 1978).

RSI measures the ratio of average gains to average losses over a lookback
window, squashed onto a 0-100 scale:

    RS  = average gain / average loss
    RSI = 100 - 100 / (1 + RS)

Reading: above 70 is "overbought", below 30 is "oversold". The classic retail
rule is mean-reversion -- buy oversold, sell overbought -- which is the exact
opposite bet to EMA and MACD. That makes RSI the most interesting indicator in
this project: if trend-following and mean-reversion both show positive
in-sample Sharpe on the same data, at least one of them is fitting noise.
"""

from __future__ import annotations

import pandas as pd

from indicators.base import hold_between_thresholds, validate_ohlcv


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI.

    Wilder smooths with a modified moving average, not a simple one. That is
    equivalent to an EWM with alpha = 1/period (NOT 2/(period+1), which is
    what a standard `span=period` EMA would give you). Getting this wrong is
    the single most common RSI implementation bug -- it produces a series that
    looks plausible, tracks the real thing loosely, and is quietly different
    from every chart you would compare it against.
    """
    if period < 2:
        raise ValueError("period must be >= 2")

    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = -delta.clip(upper=0.0)

    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))

    # A window with zero losses gives RS = inf -> RSI = 100. That is correct
    # by definition, but division produces NaN rather than inf, so pin it.
    out[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    out[(avg_gain == 0) & (avg_loss > 0)] = 0.0

    return out.rename("rsi")


def rsi_signal(
    df: pd.DataFrame,
    period: int = 14,
    oversold: float = 30.0,
    overbought: float = 70.0,
    exit_level: float = 50.0,
) -> pd.Series:
    """Target position from the classic mean-reversion RSI rule.

    Buy when RSI drops below `oversold`, short when it rises above
    `overbought`, and flatten when it returns to `exit_level`. The position
    persists between those events -- see `base.hold_between_thresholds` for
    why that matters.
    """
    validate_ohlcv(df)

    values = rsi(df["close"], period)
    position = hold_between_thresholds(values, oversold, overbought, exit_level)
    position.iloc[:period] = 0.0
    return position


PARAM_GRID = {"period": [7, 14, 21], "oversold": [20.0, 30.0], "overbought": [70.0, 80.0]}
