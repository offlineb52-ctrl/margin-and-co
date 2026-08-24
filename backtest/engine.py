"""
The backtester.

Vectorised, ~100 lines of pandas, no external backtesting framework. That is
a deliberate choice: a framework like vectorbt is faster and more featureful,
but it is also a black box you cannot defend line-by-line in an interview.
Everything that turns a signal into a P&L number is visible in this file.

THE FOUR LINES THAT MATTER
--------------------------
    held      = positions.shift(1)              # no lookahead -- see below
    gross     = held * price_returns            # what the strategy earned
    turnover  = held.diff().abs()               # how much it traded
    net       = gross - turnover * cost_rate    # what you actually kept

WHY `.shift(1)` IS NOT OPTIONAL
-------------------------------
An indicator computed from Monday's closing price is only knowable once
Monday has closed. The earliest you can act on it is Monday's close, so the
first return you can capture is Monday-close to Tuesday-close. Without the
shift, the backtest earns Monday's own return using Monday's closing price --
a time machine that typically adds 1-3 points of Sharpe out of nowhere. It is
the most common bug in homemade backtests and it is invisible unless you look
for it, because the resulting equity curve looks beautiful rather than broken.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import numpy as np
import pandas as pd

from backtest import metrics as m
from backtest.splits import Split
from config import DEFAULT_COSTS, CostModel


@dataclass
class BacktestResult:
    """Everything one strategy run produced, gross and net of costs."""

    name: str
    ticker: str
    positions: pd.Series
    gross_returns: pd.Series
    net_returns: pd.Series
    cost_drag: pd.Series
    metrics_gross: Dict[str, float] = field(default_factory=dict)
    metrics_net: Dict[str, float] = field(default_factory=dict)

    @property
    def equity_gross(self) -> pd.Series:
        return (1.0 + self.gross_returns.fillna(0.0)).cumprod()

    @property
    def equity_net(self) -> pd.Series:
        return (1.0 + self.net_returns.fillna(0.0)).cumprod()

    @property
    def total_cost_paid(self) -> float:
        """Total return given up to trading frictions, as a decimal."""
        return float(self.cost_drag.sum())

    def summary_line(self) -> str:
        g, n = self.metrics_gross, self.metrics_net
        return (
            f"{self.name:6} {self.ticker:8} "
            f"Sharpe {g['sharpe']:6.2f} gross -> {n['sharpe']:6.2f} net  "
            f"({n['num_trades']:4d} trades, {self.total_cost_paid:6.1%} paid in costs)"
        )


def price_returns(df: pd.DataFrame) -> pd.Series:
    """Simple close-to-close returns.

    Prices are already split- and dividend-adjusted by the loader, so this is
    a total return, not a price return. Using unadjusted closes would show a
    fake loss on every ex-dividend date.
    """
    return df["close"].pct_change().rename("asset_return")


def run_backtest(
    df: pd.DataFrame,
    positions: pd.Series,
    name: str = "strategy",
    ticker: str = "",
    costs: CostModel = DEFAULT_COSTS,
) -> BacktestResult:
    """Turn a target-position series into gross and net return streams."""
    if not positions.index.equals(df.index):
        raise ValueError("positions and price data must share an index")

    asset_ret = price_returns(df)

    # The position actually held during day t was decided at the close of t-1.
    held = positions.shift(1).fillna(0.0)

    gross = (held * asset_ret).rename("gross_return")

    # Turnover on day t = how much the position changed going into day t.
    # Flat -> long is 1.0; long -> short is 2.0 (you sell, then sell again).
    turnover = held.diff().abs().fillna(held.abs())

    cost_drag = (turnover * costs.total_cost_per_unit_turnover).rename("cost")
    net = (gross - cost_drag).rename("net_return")

    # Drop the first row: pct_change has no return for it.
    valid = asset_ret.notna()
    gross, net, cost_drag, held = gross[valid], net[valid], cost_drag[valid], held[valid]

    return BacktestResult(
        name=name,
        ticker=ticker,
        positions=held,
        gross_returns=gross,
        net_returns=net,
        cost_drag=cost_drag,
        metrics_gross=m.summarise(gross, held),
        metrics_net=m.summarise(net, held),
    )


def buy_and_hold(df: pd.DataFrame, ticker: str = "", costs: CostModel = DEFAULT_COSTS) -> BacktestResult:
    """The benchmark every strategy must beat to have earned its complexity.

    Always long, one trade at the start. If an indicator cannot beat this
    after costs, the honest conclusion is that the indicator adds nothing --
    and that conclusion is a perfectly good weekly report.
    """
    positions = pd.Series(1.0, index=df.index, name="position")
    return run_backtest(df, positions, name="HOLD", ticker=ticker, costs=costs)


def evaluate_on_split(
    df: pd.DataFrame,
    signal_fn: Callable[..., pd.Series],
    split: Split,
    name: str,
    ticker: str,
    costs: CostModel = DEFAULT_COSTS,
    signal_kwargs: Optional[dict] = None,
) -> Dict[str, Dict[str, float]]:
    """Score one indicator on the train half and the test half of a split.

    IMPORTANT: signals are computed on the FULL price history and only then
    sliced into train and test. That is deliberate and realistic -- a trader
    standing in 2020 has every bar from 2010 onward available for their
    moving-average warm-up. Computing signals separately on the test slice
    would throw away that history and hand the strategy an artificial
    50-day blind spot at the start of every test window.

    What is NOT shared across the boundary is any decision: no parameter is
    chosen using test-period data. The test numbers are a measurement, not
    a search.
    """
    signal_kwargs = signal_kwargs or {}
    positions = signal_fn(df, **signal_kwargs)

    train_mask = split.train_mask(df.index)
    test_mask = split.test_mask(df.index)

    out = {}
    for period, mask in (("in_sample", train_mask), ("out_of_sample", test_mask)):
        sub_df = df.loc[mask]
        sub_pos = positions.loc[mask]
        if len(sub_df) < 30:
            out[period] = {"sharpe": np.nan, "n_days": len(sub_df)}
            continue

        result = run_backtest(sub_df, sub_pos, name=name, ticker=ticker, costs=costs)
        out[period] = {
            "sharpe_gross": result.metrics_gross["sharpe"],
            "sharpe_net": result.metrics_net["sharpe"],
            "total_return_gross": result.metrics_gross["total_return"],
            "total_return_net": result.metrics_net["total_return"],
            "max_drawdown_net": result.metrics_net["max_drawdown"],
            "win_rate_net": result.metrics_net["win_rate"],
            "num_trades": result.metrics_net["num_trades"],
            "cost_paid": result.total_cost_paid,
            "n_days": result.metrics_net["n_days"],
        }

    return out
