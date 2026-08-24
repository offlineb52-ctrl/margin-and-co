"""
Performance metrics.

Each function takes a series of PERIODIC RETURNS (daily, as decimals) and
returns a single number. Keeping them separate and stateless means each one
can be checked by hand against a spreadsheet, which is exactly what you want
to be able to do when someone challenges a figure.
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from config import RISK_FREE_RATE, TRADING_DAYS_PER_YEAR


def total_return(returns: pd.Series) -> float:
    """Cumulative compounded return over the whole period, as a decimal.

    Compounded, not summed: earning +10% then -10% leaves you at -1%, not 0%.
    """
    if returns.empty:
        return float("nan")
    return float((1.0 + returns.fillna(0.0)).prod() - 1.0)


def cagr(returns: pd.Series) -> float:
    """Compound annual growth rate -- total return annualised by calendar time."""
    if returns.empty:
        return float("nan")
    years = len(returns) / TRADING_DAYS_PER_YEAR
    if years <= 0:
        return float("nan")
    growth = 1.0 + total_return(returns)
    if growth <= 0:
        return -1.0  # wiped out; CAGR is undefined, report total loss
    return float(growth ** (1.0 / years) - 1.0)


def annualised_volatility(returns: pd.Series) -> float:
    """Standard deviation of daily returns, scaled by sqrt(252).

    The sqrt scaling assumes returns are independent across days. They are
    not, quite -- volatility clusters -- so this understates risk in a crisis.
    Everyone uses it anyway; just know that you know.
    """
    if len(returns) < 2:
        return float("nan")
    return float(returns.std(ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def sharpe_ratio(returns: pd.Series, risk_free_rate: float = RISK_FREE_RATE) -> float:
    """Annualised Sharpe ratio: excess return per unit of volatility.

    The headline number, and the one most often quoted dishonestly. Three
    things inflate it in amateur backtests: ignoring costs, ignoring the
    risk-free rate, and testing on the same data used to pick the parameters.
    This project controls for all three, which is the whole point.
    """
    if len(returns) < 2:
        return float("nan")

    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = returns - daily_rf

    sd = excess.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return float("nan")

    return float(excess.mean() / sd * np.sqrt(TRADING_DAYS_PER_YEAR))


def max_drawdown(returns: pd.Series) -> float:
    """Worst peak-to-trough decline, as a negative decimal (-0.35 = -35%).

    Drawdown is what actually makes people abandon a strategy. A 0.8 Sharpe
    with a 60% drawdown is not tradeable by a human, whatever the maths says.
    """
    if returns.empty:
        return float("nan")

    # Prepend the starting capital of 1.0. Without it, a strategy that loses
    # 50% on its very first day shows a 0% drawdown, because the first equity
    # value is itself treated as the running peak. The peak you actually care
    # about is the money you started with.
    equity = (1.0 + returns.fillna(0.0)).cumprod()
    equity = pd.concat([pd.Series([1.0]), equity.reset_index(drop=True)])

    running_peak = equity.cummax()
    return float((equity / running_peak - 1.0).min())


def _trade_blocks(positions: pd.Series) -> pd.Series:
    """Label each run of consecutive identical non-zero positions as one trade."""
    changed = positions != positions.shift(1)
    return changed.cumsum().where(positions != 0)


def win_rate(returns: pd.Series, positions: pd.Series) -> float:
    """Fraction of TRADES that were profitable (not fraction of days).

    Day-level win rate flatters a strategy that is in the market constantly:
    markets rise on ~53% of days, so simply being long scores 53% without any
    skill. Trade-level win rate asks the honest question -- of the discrete
    bets this strategy made, how many made money?
    """
    blocks = _trade_blocks(positions)
    if blocks.dropna().empty:
        return float("nan")

    per_trade = returns.groupby(blocks).sum()
    if per_trade.empty:
        return float("nan")
    return float((per_trade > 0).mean())


def num_trades(positions: pd.Series) -> int:
    """Count of position changes -- i.e. how many times costs were paid."""
    return int((positions.diff().abs() > 0).sum())


def total_turnover(positions: pd.Series) -> float:
    """Sum of absolute position changes. Turnover of 2.0 = one long/short flip."""
    return float(positions.diff().abs().fillna(0.0).sum())


def time_in_market(positions: pd.Series) -> float:
    """Fraction of days holding any position. Low values make Sharpe unstable."""
    if positions.empty:
        return float("nan")
    return float((positions != 0).mean())


def summarise(returns: pd.Series, positions: pd.Series) -> Dict[str, float]:
    """All metrics for one return stream, as a flat dict ready for a DataFrame."""
    return {
        "sharpe": sharpe_ratio(returns),
        "total_return": total_return(returns),
        "cagr": cagr(returns),
        "volatility": annualised_volatility(returns),
        "max_drawdown": max_drawdown(returns),
        "win_rate": win_rate(returns, positions),
        "num_trades": num_trades(positions),
        "turnover": total_turnover(positions),
        "time_in_market": time_in_market(positions),
        "n_days": len(returns),
    }
