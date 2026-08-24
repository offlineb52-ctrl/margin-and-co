"""
Data layer: download daily OHLCV bars from Yahoo Finance, clean them, and
cache them on disk so that re-running the pipeline does not re-hit the API.

Design notes
------------
* We request auto-adjusted prices. Yahoo then adjusts open/high/low/close for
  splits and dividends, and adjusts volume for splits. Without this, a 4-for-1
  split looks like a 75% crash and every indicator fires a false signal.
* One CSV per ticker. Boring, greppable, and diff-able in git if you ever want
  to prove the data did not change between two reports.
* Everything returns a plain pandas DataFrame with lowercase column names, so
  the rest of the codebase never has to care where the data came from.
"""

from __future__ import annotations

import datetime as dt
import time
import warnings
from pathlib import Path
from typing import List, Optional

import pandas as pd
import yfinance as yf

from config import CACHE_DIR, CACHE_TTL_DAYS, DEFAULT_END, DEFAULT_START

# yfinance is chatty about future pandas deprecations; they are not ours to fix.
warnings.filterwarnings("ignore", category=FutureWarning, module="yfinance")

REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


# --------------------------------------------------------------------------
# Cache helpers
# --------------------------------------------------------------------------

def _cache_path(ticker: str) -> Path:
    """Where a ticker's bars live on disk. '.' is illegal-ish in filenames."""
    safe = ticker.replace(".", "_").replace("/", "_").upper()
    return CACHE_DIR / f"{safe}.csv"


def _cache_is_fresh(path: Path, ttl_days: int = CACHE_TTL_DAYS) -> bool:
    """True if the cache file exists and was written recently enough.

    Daily bars never change once the session has closed, so the only reason
    to refresh is to pick up new days at the right-hand edge.
    """
    if not path.exists():
        return False
    age_seconds = time.time() - path.stat().st_mtime
    return age_seconds < ttl_days * 86_400


# --------------------------------------------------------------------------
# Cleaning
# --------------------------------------------------------------------------

def _flatten_columns(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """yfinance returns MultiIndex columns ('Close', 'AAPL'); we want 'close'.

    The exact shape depends on the yfinance version and on whether one ticker
    or many were requested, so we handle both rather than assuming.
    """
    if isinstance(df.columns, pd.MultiIndex):
        # Find whichever level holds the ticker symbol and drop it.
        levels_with_ticker = [
            i for i in range(df.columns.nlevels)
            if ticker.upper() in {str(v).upper() for v in df.columns.get_level_values(i)}
        ]
        if levels_with_ticker:
            df = df.droplevel(levels_with_ticker[0], axis=1)
        else:
            df.columns = df.columns.get_level_values(0)

    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    return df


def _clean(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Normalise, validate, and drop unusable rows.

    Rows are dropped rather than filled: forward-filling a missing close
    creates a zero-return day that never happened, which quietly flatters
    volatility and therefore the Sharpe ratio.
    """
    df = _flatten_columns(df.copy(), ticker)

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"{ticker}: missing columns {missing}; got {list(df.columns)}")

    df = df[REQUIRED_COLUMNS]

    # Index must be a clean, sorted, unique DatetimeIndex.
    df.index = pd.to_datetime(df.index, utc=True).tz_localize(None).normalize()
    df.index.name = "date"
    df = df[~df.index.duplicated(keep="last")].sort_index()

    # Coerce to numeric; anything unparseable becomes NaN and is then dropped.
    for col in REQUIRED_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    before = len(df)
    df = df.dropna(subset=["close"])

    # A non-positive price is a data error, not a market event.
    df = df[df["close"] > 0]

    # Zero-volume days are usually holidays or halts that leaked into the feed.
    # Keep them, but flag: they matter for VWAP, which divides by volume.
    dropped = before - len(df)
    if dropped:
        print(f"  [clean] {ticker}: dropped {dropped} unusable row(s)")

    return df


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------

def load_ticker(
    ticker: str,
    start: str = DEFAULT_START,
    end: Optional[str] = DEFAULT_END,
    use_cache: bool = True,
    verbose: bool = True,
) -> pd.DataFrame:
    """Return cleaned daily OHLCV bars for one ticker.

    Reads from the local cache when possible, otherwise downloads and caches.
    """
    path = _cache_path(ticker)

    if use_cache and _cache_is_fresh(path):
        if verbose:
            print(f"  [cache] {ticker}: reading {path.name}")
        df = pd.read_csv(path, index_col="date", parse_dates=["date"])
        return df.loc[start:end] if end else df.loc[start:]

    if verbose:
        print(f"  [fetch] {ticker}: downloading from Yahoo Finance...")

    raw = yf.download(
        ticker,
        start=start,
        end=end,
        auto_adjust=True,   # split- and dividend-adjusted; see module docstring
        progress=False,
        threads=False,
    )

    if raw is None or raw.empty:
        raise ValueError(
            f"{ticker}: Yahoo returned no data. Check the symbol "
            f"(FTSE names need a '.L' suffix, e.g. 'SHEL.L')."
        )

    df = _clean(raw, ticker)
    df.to_csv(path)
    if verbose:
        print(f"  [cache] {ticker}: wrote {len(df)} rows -> {path.name}")

    return df


def load_universe(
    tickers: List[str],
    start: str = DEFAULT_START,
    end: Optional[str] = DEFAULT_END,
    use_cache: bool = True,
    verbose: bool = True,
) -> dict:
    """Load many tickers. Failures are reported and skipped, not fatal.

    One dead symbol should never kill a weekly report run.
    """
    out = {}
    for ticker in tickers:
        try:
            out[ticker] = load_ticker(ticker, start, end, use_cache, verbose)
        except Exception as exc:  # noqa: BLE001 -- we want the run to continue
            print(f"  [skip]  {ticker}: {exc}")
    if verbose:
        print(f"  [done]  loaded {len(out)}/{len(tickers)} tickers")
    return out


def describe(df: pd.DataFrame, ticker: str = "") -> str:
    """A one-line human summary, used in reports and for eyeballing loads."""
    return (
        f"{ticker or 'series'}: {len(df):,} bars, "
        f"{df.index[0].date()} to {df.index[-1].date()}, "
        f"last close {df['close'].iloc[-1]:,.2f}"
    )
