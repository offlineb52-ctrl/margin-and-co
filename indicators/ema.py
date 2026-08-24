"""
EMA crossover -- the most widely used trend-following rule in retail trading.

The idea: a short-window average reacts faster than a long-window average, so
when the fast line crosses above the slow line, price momentum has turned up.
Go long. When it crosses below, go short.

The known weakness, which this project is designed to measure: in a sideways
market the two lines cross constantly, generating a stream of small losing
trades. Each one pays the full round-trip cost. This is why EMA strategies
often look fine gross and die net.
"""

from __future__ import annotations

import pandas as pd

from indicators.base import crossover_position, validate_ohlcv


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponentially weighted moving average.

    `adjust=False` gives the recursive form every charting package uses:
        EMA_t = alpha * price_t + (1 - alpha) * EMA_{t-1},  alpha = 2/(span+1)

    pandas' default (`adjust=True`) computes a slightly different weighted
    average at the start of the series. The difference fades but it is enough
    to make your numbers disagree with TradingView, so we pin it explicitly.
    """
    if span < 1:
        raise ValueError("span must be >= 1")
    return series.ewm(span=span, adjust=False).mean()


def ema_signal(
    df: pd.DataFrame,
    fast_span: int = 20,
    slow_span: int = 50,
) -> pd.Series:
    """Target position from a fast/slow EMA crossover.

    Defaults are 20/50 rather than the famous 50/200 'golden cross' because
    50/200 produces only a handful of trades per decade -- too few to say
    anything statistically meaningful within this project's data window.
    """
    validate_ohlcv(df)
    if fast_span >= slow_span:
        raise ValueError("fast_span must be strictly less than slow_span")

    fast = ema(df["close"], fast_span)
    slow = ema(df["close"], slow_span)

    # Suppress the warm-up period: an EMA is unreliable until it has seen
    # roughly `span` observations, and trading on that noise is not a signal.
    position = crossover_position(fast, slow)
    position.iloc[:slow_span] = 0.0
    return position


PARAM_GRID = {"fast_span": [10, 20, 50], "slow_span": [50, 100, 200]}
