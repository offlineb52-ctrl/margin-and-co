"""
The Survival Score.

A single number, 1 to 10, for one indicator applied to one ticker. It answers
a narrow question: **if you had traded this rule on this stock, would anything
have survived contact with unseen data and real trading costs?**

WHY A SCORE AT ALL
------------------
The weekly reports already publish Sharpe ratios, drawdowns and trade counts.
A composite score adds nothing a careful reader could not work out — its job is
to make a hundred results comparable at a glance and rankable over time. It is
a summary, not a discovery, and the report says so.

The formula is fully specified below. There is no fitted model, no machine
learning, and no parameter chosen because it made the output look better. Every
constant is a judgement that can be argued with, which is the point: you can
disagree with a weight and recompute the whole table yourself.

THE FORMULA
-----------
Three components, each scored 0-10, then weighted:

    Survival Score = 0.60 x Performance
                   + 0.25 x Consistency
                   + 0.15 x Drawdown resilience

1. **Performance (60%)** — out-of-sample Sharpe ratio, NET of trading costs.
   This carries the most weight because it is the only component that answers
   "did it make money on data it had never seen, after paying to trade?"

       Sharpe <= -0.50  ->  0
       Sharpe >= +1.50  -> 10
       linear in between

   The +1.50 anchor is deliberate: it is roughly what buy & hold delivered on
   this universe over the study window. A rule scoring 10 on performance is a
   rule that matched simply owning the market, which puts the scale in
   perspective.

2. **Consistency (25%)** — the share of walk-forward windows in which the
   strategy posted a positive net Sharpe, times ten. Seven windows positive
   out of ten scores 7.

   This is the component that catches a rule which earned everything in one
   extraordinary year and nothing since. Mean performance hides that;
   consistency cannot.

3. **Drawdown resilience (15%)** — worst peak-to-trough decline out-of-sample.

       drawdown of 0%    -> 10
       drawdown of -50%  ->  0
       linear in between

   Weighted lowest, and capped below, because drawdown is the easiest
   component to game: a rule that is almost never in the market has almost no
   drawdown. See the guard rails.

GUARD RAILS
-----------
Two rules override the arithmetic, because without them the composite would
flatter strategies that deserve nothing.

  * **A losing strategy cannot score above 3.0.** If out-of-sample net Sharpe
    is at or below zero, the rule lost money after costs. No amount of low
    drawdown or steady mediocrity should lift that into the middle of a
    ten-point scale.

  * **A strategy that barely trades cannot score above 5.0.** If it is in the
    market less than 5% of the time, its statistics rest on too few
    observations to mean much, and its drawdown score is an artefact of
    sitting in cash.

  * **Fewer than ten out-of-sample trades cannot score above 5.0.** A rule can
    be invested constantly and still have made only a handful of distinct
    bets. The first full-universe run produced a combination scoring 8.0 on
    four trades, which is not evidence of anything.

Both are applied after the weighted sum and are recorded in the output, so a
capped score is visibly capped rather than silently different.

READING THE SCORES
------------------
    8-10  Survived convincingly. Rare, and worth a second look.
    6-8   Positive after costs, but not clearly better than doing nothing.
    4-6   Marginal. Most likely noise.
    1-4   Did not survive. This is where the great majority land.

If almost everything scores below 4, that is not a broken scale. That is the
finding.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backtest import metrics as m
from backtest.engine import run_backtest
from backtest.splits import Split, simple_split, walk_forward_windows
from config import DEFAULT_COSTS, CostModel

# --------------------------------------------------------------------------
# Anchors. Every one of these is a judgement, stated openly so it can be
# argued with. Change one and the whole table shifts -- which is why they live
# here as named constants rather than buried in the arithmetic.
# --------------------------------------------------------------------------

SHARPE_FLOOR = -0.50      # scores 0 on performance
SHARPE_CEILING = 1.50     # scores 10; roughly buy & hold on this universe
DRAWDOWN_FLOOR = -0.50    # -50% scores 0 on resilience
WEIGHT_PERFORMANCE = 0.60
WEIGHT_CONSISTENCY = 0.25
WEIGHT_DRAWDOWN = 0.15

LOSING_STRATEGY_CAP = 3.0     # applied when out-of-sample net Sharpe <= 0
INACTIVE_STRATEGY_CAP = 5.0   # applied when time in market < 5%
MIN_TIME_IN_MARKET = 0.05

# A rule can be in the market constantly and still have made only a handful of
# distinct bets -- a trend follower that flipped four times in a decade is
# always invested but has four observations. Four is not evidence, however
# good the Sharpe ratio looks, so the same cap applies.
FEW_TRADES_CAP = 5.0
MIN_TRADES = 10

SCORE_MIN, SCORE_MAX = 1.0, 10.0

# Minimum price history before a combination is scored at all.
#
# 1,260 trading days is about five years. Below that, a 70/30 split leaves a
# test window too short to say much, and the walk-forward scheme (three years
# train, one year test) produces no windows at all -- which would drive the
# consistency component to zero for a reason that has nothing to do with the
# indicator. Rather than publish a score that is really a comment on the data,
# short-history tickers are skipped and counted.
MIN_BARS_TO_SCORE = 1260


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _linear(value: float, low: float, high: float) -> float:
    """Map `value` from the range [low, high] onto 0-10, clamped at both ends."""
    if high == low:
        return 0.0
    return _clamp((value - low) / (high - low) * 10.0, 0.0, 10.0)


# --------------------------------------------------------------------------
# The three components
# --------------------------------------------------------------------------

def performance_score(out_sample_sharpe_net: Optional[float]) -> float:
    if out_sample_sharpe_net is None or np.isnan(out_sample_sharpe_net):
        return 0.0
    return _linear(out_sample_sharpe_net, SHARPE_FLOOR, SHARPE_CEILING)


def consistency_score(pct_windows_positive: Optional[float]) -> float:
    """Share of walk-forward windows that were positive, on a 0-10 scale."""
    if pct_windows_positive is None or np.isnan(pct_windows_positive):
        return 0.0
    return _clamp(pct_windows_positive * 10.0, 0.0, 10.0)


def drawdown_score(max_drawdown_net: Optional[float]) -> float:
    """Worst peak-to-trough decline. Input is negative (-0.25 = -25%)."""
    if max_drawdown_net is None or np.isnan(max_drawdown_net):
        return 0.0
    return _linear(max_drawdown_net, DRAWDOWN_FLOOR, 0.0)


@dataclass
class SurvivalScore:
    """One score, with every input kept so the number can be audited."""

    indicator: str
    ticker: str
    score: float

    performance: float
    consistency: float
    drawdown: float
    raw_score: float          # before the guard rails
    capped_by: Optional[str]  # which guard rail applied, if any

    out_sample_sharpe_net: Optional[float]
    out_sample_sharpe_gross: Optional[float]
    in_sample_sharpe_gross: Optional[float]
    max_drawdown_net: Optional[float]
    pct_windows_positive: Optional[float]
    n_windows: int
    num_trades: int
    time_in_market: Optional[float]
    cost_paid: Optional[float]
    verdict: str

    def to_dict(self) -> Dict:
        return asdict(self)


def band(score: float) -> str:
    """Plain-English band, so a reader never has to interpret a bare number."""
    if score >= 8.0:
        return "Survived"
    if score >= 6.0:
        return "Positive, beaten by holding"
    if score >= 4.0:
        return "Marginal"
    return "Did not survive"


def compute(
    *,
    indicator: str,
    ticker: str,
    out_sample_sharpe_net: Optional[float],
    out_sample_sharpe_gross: Optional[float] = None,
    in_sample_sharpe_gross: Optional[float] = None,
    max_drawdown_net: Optional[float] = None,
    pct_windows_positive: Optional[float] = None,
    n_windows: int = 0,
    num_trades: int = 0,
    time_in_market: Optional[float] = None,
    cost_paid: Optional[float] = None,
) -> SurvivalScore:
    """Assemble one Survival Score from a set of measured statistics."""
    perf = performance_score(out_sample_sharpe_net)
    cons = consistency_score(pct_windows_positive)
    draw = drawdown_score(max_drawdown_net)

    raw = (WEIGHT_PERFORMANCE * perf
           + WEIGHT_CONSISTENCY * cons
           + WEIGHT_DRAWDOWN * draw)

    capped_by = None
    score = raw

    # Guard rails, applied in order of severity.
    if out_sample_sharpe_net is None or np.isnan(out_sample_sharpe_net) \
            or out_sample_sharpe_net <= 0:
        if score > LOSING_STRATEGY_CAP:
            score, capped_by = LOSING_STRATEGY_CAP, "lost money after costs"

    if time_in_market is not None and not np.isnan(time_in_market) \
            and time_in_market < MIN_TIME_IN_MARKET:
        if score > INACTIVE_STRATEGY_CAP:
            score, capped_by = INACTIVE_STRATEGY_CAP, "barely traded"

    if num_trades < MIN_TRADES and score > FEW_TRADES_CAP:
        score, capped_by = FEW_TRADES_CAP, f"only {num_trades} trades"

    score = round(_clamp(score, SCORE_MIN, SCORE_MAX), 1)

    return SurvivalScore(
        indicator=indicator,
        ticker=ticker,
        score=score,
        performance=round(perf, 2),
        consistency=round(cons, 2),
        drawdown=round(draw, 2),
        raw_score=round(raw, 2),
        capped_by=capped_by,
        out_sample_sharpe_net=out_sample_sharpe_net,
        out_sample_sharpe_gross=out_sample_sharpe_gross,
        in_sample_sharpe_gross=in_sample_sharpe_gross,
        max_drawdown_net=max_drawdown_net,
        pct_windows_positive=pct_windows_positive,
        n_windows=n_windows,
        num_trades=num_trades,
        time_in_market=time_in_market,
        cost_paid=cost_paid,
        verdict=band(score),
    )


# --------------------------------------------------------------------------
# Measuring one indicator on one ticker
# --------------------------------------------------------------------------

def evaluate(
    df: pd.DataFrame,
    signal_fn,
    indicator: str,
    ticker: str,
    costs: CostModel = DEFAULT_COSTS,
) -> Optional[SurvivalScore]:
    """Run one indicator on one ticker and return its Survival Score.

    Signals are computed on the full price history and then sliced, so the
    warm-up period of a moving average is not wasted at the start of the test
    window. No parameter is chosen using test-period data -- the test half is
    measured, never searched.
    """
    if len(df) < MIN_BARS_TO_SCORE:
        return None

    positions = signal_fn(df)
    split = simple_split(df.index)

    train = split.train_mask(df.index)
    test = split.test_mask(df.index)

    in_sample = run_backtest(df.loc[train], positions.loc[train],
                             name=indicator, ticker=ticker, costs=costs)
    out_sample = run_backtest(df.loc[test], positions.loc[test],
                              name=indicator, ticker=ticker, costs=costs)

    # Walk-forward: many independent out-of-sample readings, not one.
    sharpes: List[float] = []
    for window in walk_forward_windows(df.index):
        mask = window.test_mask(df.index)
        if mask.sum() < 60:
            continue
        result = run_backtest(df.loc[mask], positions.loc[mask],
                              name=indicator, ticker=ticker, costs=costs)
        value = result.metrics_net["sharpe"]
        if not np.isnan(value):
            sharpes.append(value)

    pct_positive = (float(np.mean([s > 0 for s in sharpes]))
                    if sharpes else None)

    return compute(
        indicator=indicator,
        ticker=ticker,
        out_sample_sharpe_net=out_sample.metrics_net["sharpe"],
        out_sample_sharpe_gross=out_sample.metrics_gross["sharpe"],
        in_sample_sharpe_gross=in_sample.metrics_gross["sharpe"],
        max_drawdown_net=out_sample.metrics_net["max_drawdown"],
        pct_windows_positive=pct_positive,
        n_windows=len(sharpes),
        num_trades=int(out_sample.metrics_net["num_trades"]),
        time_in_market=out_sample.metrics_net["time_in_market"],
        cost_paid=out_sample.total_cost_paid,
    )


def scoreboard(
    frames: Dict[str, pd.DataFrame],
    indicators: Dict,
    costs: CostModel = DEFAULT_COSTS,
    verbose: bool = True,
) -> pd.DataFrame:
    """Score every indicator against every ticker. Returns a ranked table."""
    rows = []
    for ticker, df in frames.items():
        for name, (signal_fn, _description) in indicators.items():
            result = evaluate(df, signal_fn, name, ticker, costs)
            if result:
                rows.append(result.to_dict())
        if verbose:
            print(f"  scored {ticker}")

    if not rows:
        return pd.DataFrame()

    table = pd.DataFrame(rows)
    return table.sort_values(
        ["score", "out_sample_sharpe_net"], ascending=False
    ).reset_index(drop=True)
