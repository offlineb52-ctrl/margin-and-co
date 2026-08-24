"""
Central configuration for Margin & Co.

Every assumption that could change a result lives here, in one file, so that
the answer to "what did you assume?" is a single page rather than a hunt
through the codebase. If a number below changes, the conclusions may change --
that is the point of keeping them visible.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
CACHE_DIR = PROJECT_ROOT / "data" / "cache"
REPORT_DIR = PROJECT_ROOT / "reports" / "output"

CACHE_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------

# How far back to pull. Longer history = more out-of-sample data, but also
# more regime change (2008, 2020) which makes single-split results noisy.
# This is why we ALSO run walk-forward validation, not just one 70/30 split.
DEFAULT_START = "2010-01-01"
DEFAULT_END = None  # None = today

# Cache format. Parquet is faster but needs pyarrow; CSV needs nothing extra.
CACHE_FORMAT = "csv"

# Re-download data older than this many days. Daily bars don't change once
# printed, so a long TTL is fine -- we only need fresh data at the right edge.
CACHE_TTL_DAYS = 1


# --------------------------------------------------------------------------
# Trading cost model
# --------------------------------------------------------------------------
# These are the numbers the whole project turns on. A strategy that trades
# every day pays these costs ~252 times a year; one that trades monthly pays
# them 12 times. That difference is usually larger than the difference in
# raw signal quality, which is the finding most retail backtests hide.
#
# All values are in basis points (1 bp = 0.01%) of notional traded.

@dataclass(frozen=True)
class CostModel:
    """Per-side trading costs, in basis points of notional."""

    # Half the quoted bid-ask spread. You cross the spread on entry AND exit,
    # so you pay roughly the half-spread on each side. 5bp is a reasonable
    # assumption for large-cap US equities; small caps and FTSE 350 mid-caps
    # are materially worse (15-30bp is not unusual).
    half_spread_bps: float = 5.0

    # Broker commission per side. Many retail brokers advertise "zero
    # commission" and monetise via payment-for-order-flow, which shows up as
    # worse fill prices instead -- so setting this to 0 does not mean free.
    commission_bps: float = 1.0

    # Market impact / slippage: the gap between the price you saw and the
    # price you got. For a retail-size order in a liquid name this is small,
    # but it is never zero, and it grows with order size and volatility.
    slippage_bps: float = 2.0

    @property
    def total_bps_per_side(self) -> float:
        """Total cost paid on one side of a trade, in basis points."""
        return self.half_spread_bps + self.commission_bps + self.slippage_bps

    @property
    def total_cost_per_unit_turnover(self) -> float:
        """Cost as a decimal fraction, per 1.0 of position change.

        Turnover of 1.0 means going from flat to fully long (one side).
        Turnover of 2.0 means flipping long to short (two sides).
        """
        return self.total_bps_per_side / 10_000.0


# The baseline cost assumption used in the weekly report.
DEFAULT_COSTS = CostModel()

# A deliberately optimistic model, used to show how sensitive results are to
# the cost assumption. If a strategy only survives under ZERO_COSTS, say so.
ZERO_COSTS = CostModel(half_spread_bps=0.0, commission_bps=0.0, slippage_bps=0.0)


# --------------------------------------------------------------------------
# Train / test splitting
# --------------------------------------------------------------------------

# Fraction of each ticker's history used for in-sample (training) work.
# The remaining 30% is held out and touched ONCE, at the end.
TRAIN_FRACTION = 0.70

# Walk-forward validation windows, in trading days (~252 per year).
# We train on `train_days`, test on the next `test_days`, then roll forward
# by `step_days` and repeat. This gives many out-of-sample readings instead
# of one, which is the difference between an anecdote and evidence.
WALK_FORWARD_TRAIN_DAYS = 756   # ~3 years
WALK_FORWARD_TEST_DAYS = 252    # ~1 year
WALK_FORWARD_STEP_DAYS = 252    # roll forward one year at a time


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

TRADING_DAYS_PER_YEAR = 252

# Risk-free rate used in the Sharpe ratio, annualised. Set to 0.0 to report
# the raw (excess-return-free) Sharpe, which is what most retail backtests
# quietly do. Keeping it explicit means the number is comparable.
RISK_FREE_RATE = 0.0


# --------------------------------------------------------------------------
# Universe
# --------------------------------------------------------------------------
# NOTE ON SURVIVORSHIP BIAS -- read this before quoting any result.
#
# These lists are CURRENT index members. Any company that was in the index in
# 2010 but has since been delisted, acquired, or dropped for poor performance
# is absent. That biases backtests upward, because we are only ever testing
# on companies that survived to today.
#
# The honest fix is a point-in-time constituent history (CRSP, Norgate), which
# is paid data. Since this project cannot use that, the limitation is stated
# in every report rather than quietly ignored. Practically: treat absolute
# returns as optimistic, and lean on the IN-SAMPLE vs OUT-OF-SAMPLE
# comparison, which is affected far less because both halves share the bias.

SMOKE_TEST_TICKERS: List[str] = ["AAPL"]

# A small, hand-checked starter universe. Expand once the pipeline is proven.
SP500_SAMPLE: List[str] = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META",
    "JPM", "XOM", "JNJ", "PG", "KO",
]

# FTSE 350 names carry the ".L" suffix on Yahoo Finance and are quoted in
# pence, not pounds. Returns are unaffected (it's a constant scale factor),
# but never compare raw price levels across the two universes.
FTSE350_SAMPLE: List[str] = [
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L",
]


@dataclass
class RunConfig:
    """Everything one pipeline run needs to know."""

    tickers: List[str] = field(default_factory=lambda: list(SMOKE_TEST_TICKERS))
    start: str = DEFAULT_START
    end: object = DEFAULT_END
    costs: CostModel = DEFAULT_COSTS
    train_fraction: float = TRAIN_FRACTION
    run_walk_forward: bool = True
