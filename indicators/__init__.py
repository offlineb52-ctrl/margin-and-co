"""
Indicator registry.

Adding an indicator means writing the function and adding one line here; the
backtester and report generator both iterate over INDICATORS, so nothing else
needs to change.
"""

from indicators.ema import ema_signal
from indicators.macd import macd_signal
from indicators.rsi import rsi_signal
from indicators.vwap import vwap_signal

# name -> (signal function, one-line description for the report)
INDICATORS = {
    "EMA": (
        ema_signal,
        "Trend-following: long while the 20-day EMA is above the 50-day EMA.",
    ),
    "MACD": (
        macd_signal,
        "Trend-following: long while the MACD line (EMA12-EMA26) is above its 9-day signal line.",
    ),
    "RSI": (
        rsi_signal,
        "Mean-reversion: buy below RSI 30, short above RSI 70, flatten back at 50.",
    ),
    "VWAP": (
        vwap_signal,
        "Trend-following: long while the close is above the 20-day rolling volume-weighted average price.",
    ),
}

__all__ = ["INDICATORS", "ema_signal", "macd_signal", "rsi_signal", "vwap_signal"]
