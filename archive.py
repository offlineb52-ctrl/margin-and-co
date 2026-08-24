"""
The score archive.

Every week's Survival Scores are written to a small SQLite database so that
score *history* is queryable: has RSI on AAPL been drifting up or down over
six months? Did anything that scored 8 last quarter hold up?

WHY SQLITE AND NOT MORE JSON FILES
----------------------------------
The weekly reports already emit JSON, and that stays -- it is the published
record. But answering "show me every score above 6 in the last ten weeks"
against a directory of JSON files means loading and filtering all of them by
hand every time. SQLite gives that as one query, ships with Python, needs no
server, and is a single file you can copy or commit.

It is also honest about what it is: this is a log, not a source of truth. If
the database were deleted it could be rebuilt by re-running the pipeline over
the published JSON. Nothing here is the only copy of anything.

THE ONE RULE
------------
Scores are written once per (week, indicator, ticker) and are **not** revised.
Re-running a week overwrites only if you explicitly ask, and the CLI says so.
A published score that quietly changes later would undermine the entire point
of keeping the history.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd

from config import PROJECT_ROOT

DB_PATH = PROJECT_ROOT / "data" / "archive.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    week                  INTEGER NOT NULL,
    published             TEXT    NOT NULL,
    indicator             TEXT    NOT NULL,
    ticker                TEXT    NOT NULL,

    score                 REAL    NOT NULL,
    verdict               TEXT    NOT NULL,

    -- The three components, kept so any score can be recomputed by hand.
    performance           REAL,
    consistency           REAL,
    drawdown              REAL,
    raw_score             REAL,
    capped_by             TEXT,

    -- The measured statistics the components were derived from.
    out_sample_sharpe_net    REAL,
    out_sample_sharpe_gross  REAL,
    in_sample_sharpe_gross   REAL,
    max_drawdown_net         REAL,
    pct_windows_positive     REAL,
    n_windows                INTEGER,
    num_trades               INTEGER,
    time_in_market           REAL,
    cost_paid                REAL,

    PRIMARY KEY (week, indicator, ticker)
);

CREATE INDEX IF NOT EXISTS idx_scores_pair ON scores (indicator, ticker, week);
CREATE INDEX IF NOT EXISTS idx_scores_week ON scores (week, score DESC);

CREATE TABLE IF NOT EXISTS weeks (
    week          INTEGER PRIMARY KEY,
    published     TEXT NOT NULL,
    universe_size INTEGER,
    tickers       TEXT,
    costs_bps     REAL,
    notes         TEXT
);
"""

COLUMNS = [
    "week", "published", "indicator", "ticker", "score", "verdict",
    "performance", "consistency", "drawdown", "raw_score", "capped_by",
    "out_sample_sharpe_net", "out_sample_sharpe_gross", "in_sample_sharpe_gross",
    "max_drawdown_net", "pct_windows_positive", "n_windows", "num_trades",
    "time_in_market", "cost_paid",
]


@contextmanager
def connect(path: Optional[Path] = None):
    """Open the database, creating it and its schema if needed."""
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_week(
    scores: pd.DataFrame,
    week: int,
    universe: Iterable[str],
    costs_bps: float,
    published: Optional[str] = None,
    notes: str = "",
    overwrite: bool = False,
    path: Optional[Path] = None,
) -> int:
    """Write one week's scores. Returns the number of rows written.

    Refuses to touch a week that already exists unless `overwrite` is set --
    the archive's value comes from its entries not moving after publication.
    """
    published = published or dt.date.today().isoformat()
    tickers = sorted(set(universe))

    with connect(path) as conn:
        existing = conn.execute(
            "SELECT COUNT(*) AS n FROM scores WHERE week = ?", (week,)
        ).fetchone()["n"]

        if existing and not overwrite:
            raise ValueError(
                f"week {week} already has {existing} rows. Re-running a "
                f"published week changes history; pass overwrite=True only if "
                f"you are certain."
            )

        if existing:
            conn.execute("DELETE FROM scores WHERE week = ?", (week,))

        conn.execute(
            "INSERT OR REPLACE INTO weeks "
            "(week, published, universe_size, tickers, costs_bps, notes) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (week, published, len(tickers), ",".join(tickers), costs_bps, notes),
        )

        rows = []
        for record in scores.to_dict("records"):
            record = {**record, "week": week, "published": published}
            rows.append(tuple(record.get(column) for column in COLUMNS))

        conn.executemany(
            f"INSERT INTO scores ({', '.join(COLUMNS)}) "
            f"VALUES ({', '.join('?' * len(COLUMNS))})",
            rows,
        )

    return len(rows)


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------

def week_table(week: int, path: Optional[Path] = None) -> pd.DataFrame:
    """Every score for one week, ranked."""
    with connect(path) as conn:
        return pd.read_sql_query(
            "SELECT * FROM scores WHERE week = ? ORDER BY score DESC, "
            "out_sample_sharpe_net DESC", conn, params=(week,)
        )


def history(indicator: str, ticker: str, path: Optional[Path] = None) -> pd.DataFrame:
    """Score history for one indicator/ticker pair, oldest first.

    This is the query the archive exists for: the trend, not the snapshot.
    """
    with connect(path) as conn:
        return pd.read_sql_query(
            "SELECT week, published, score, out_sample_sharpe_net, verdict "
            "FROM scores WHERE indicator = ? AND ticker = ? ORDER BY week",
            conn, params=(indicator, ticker),
        )


def leaderboard(week: Optional[int] = None, limit: int = 10,
                path: Optional[Path] = None) -> pd.DataFrame:
    """Top scores for a week, defaulting to the most recent."""
    with connect(path) as conn:
        if week is None:
            row = conn.execute("SELECT MAX(week) AS w FROM scores").fetchone()
            week = row["w"]
        if week is None:
            return pd.DataFrame()
        return pd.read_sql_query(
            "SELECT indicator, ticker, score, verdict, out_sample_sharpe_net "
            "FROM scores WHERE week = ? ORDER BY score DESC, "
            "out_sample_sharpe_net DESC LIMIT ?",
            conn, params=(week, limit),
        )


def weeks(path: Optional[Path] = None) -> pd.DataFrame:
    with connect(path) as conn:
        return pd.read_sql_query("SELECT * FROM weeks ORDER BY week DESC", conn)


def latest_week(path: Optional[Path] = None) -> Optional[int]:
    with connect(path) as conn:
        row = conn.execute("SELECT MAX(week) AS w FROM scores").fetchone()
        return row["w"]


def trend(min_weeks: int = 3, path: Optional[Path] = None) -> pd.DataFrame:
    """Pairs with enough history to show a direction: first vs latest score.

    Deliberately requires several observations. A "trend" drawn through two
    points is a line, not a trend, and publishing it as one would be exactly
    the sort of overclaiming this project exists to avoid.
    """
    with connect(path) as conn:
        return pd.read_sql_query(
            """
            SELECT indicator, ticker,
                   COUNT(*)                       AS weeks_tracked,
                   MIN(week)                      AS first_week,
                   MAX(week)                      AS last_week,
                   ROUND(AVG(score), 2)           AS mean_score,
                   ROUND(MAX(score) - MIN(score), 2) AS score_range
            FROM scores
            GROUP BY indicator, ticker
            HAVING COUNT(*) >= ?
            ORDER BY mean_score DESC
            """,
            conn, params=(min_weeks,),
        )
