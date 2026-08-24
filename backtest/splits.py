"""
Splitting history into training and testing periods.

WHY THIS FILE IS THE MOST IMPORTANT ONE IN THE PROJECT
-------------------------------------------------------
Anyone can find a parameter set that made money in the past -- there are
thousands of combinations of EMA spans and RSI thresholds, and by chance some
of them will look excellent on any given price series. That is not a
discovery; it is a search result.

The only defence is to decide on the rules using one slice of history and
then measure them on a slice you have never looked at. The gap between those
two numbers IS the finding this project publishes.

Two schemes are provided:
  * simple_split       -- one 70/30 cut. Easy to explain, but one reading.
  * walk_forward_windows -- many rolling train/test pairs. Slower, but it
    answers "did this hold up repeatedly?" rather than "did it hold up once?"
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

import pandas as pd

from config import (
    TRAIN_FRACTION,
    WALK_FORWARD_STEP_DAYS,
    WALK_FORWARD_TEST_DAYS,
    WALK_FORWARD_TRAIN_DAYS,
)


@dataclass(frozen=True)
class Split:
    """One train/test pair, stored as index slices rather than copied data."""

    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    label: str = ""

    def train_mask(self, index: pd.DatetimeIndex) -> pd.Series:
        return (index >= self.train_start) & (index <= self.train_end)

    def test_mask(self, index: pd.DatetimeIndex) -> pd.Series:
        return (index >= self.test_start) & (index <= self.test_end)

    def __str__(self) -> str:
        return (
            f"{self.label or 'split'}: train {self.train_start.date()}->"
            f"{self.train_end.date()} | test {self.test_start.date()}->"
            f"{self.test_end.date()}"
        )


def simple_split(index: pd.DatetimeIndex, train_fraction: float = TRAIN_FRACTION) -> Split:
    """One chronological cut: first `train_fraction` trains, the rest tests.

    Chronological, never random. Shuffling time series data lets tomorrow's
    information leak into today's training set, which makes almost any
    strategy look predictive.
    """
    if not 0.0 < train_fraction < 1.0:
        raise ValueError("train_fraction must be strictly between 0 and 1")
    if len(index) < 100:
        raise ValueError(f"need at least 100 bars to split, got {len(index)}")

    cut = int(len(index) * train_fraction)
    return Split(
        train_start=index[0],
        train_end=index[cut - 1],
        test_start=index[cut],
        test_end=index[-1],
        label=f"{int(train_fraction * 100)}/{int((1 - train_fraction) * 100)} split",
    )


def walk_forward_windows(
    index: pd.DatetimeIndex,
    train_days: int = WALK_FORWARD_TRAIN_DAYS,
    test_days: int = WALK_FORWARD_TEST_DAYS,
    step_days: int = WALK_FORWARD_STEP_DAYS,
) -> List[Split]:
    """Rolling train/test pairs marching forward through history.

    Window 1 trains on years 1-3 and tests on year 4; window 2 trains on
    years 2-4 and tests on year 5, and so on. Each test period is genuinely
    out-of-sample relative to its own training window, so a strategy that
    only worked in one lucky year is exposed immediately.
    """
    windows: List[Split] = []
    start = 0
    n = len(index)

    while start + train_days + test_days <= n:
        train_lo, train_hi = start, start + train_days - 1
        test_lo, test_hi = start + train_days, start + train_days + test_days - 1

        windows.append(
            Split(
                train_start=index[train_lo],
                train_end=index[train_hi],
                test_start=index[test_lo],
                test_end=index[test_hi],
                label=f"WF{len(windows) + 1} (test {index[test_lo].year})",
            )
        )
        start += step_days

    return windows


def describe_splits(splits: List[Split]) -> str:
    """Human-readable listing, printed at the top of a run for sanity checking."""
    if not splits:
        return "  (no valid windows -- history too short for these settings)"
    return "\n".join(f"  {s}" for s in splits)
