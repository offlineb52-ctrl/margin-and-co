"""
Data for the self-serve lookup tool.

The site lets a visitor type a ticker and get that company's Survival Score
across all four indicators. This module produces what that page reads.

Three constraints shape the design, and they pull against each other:

1. The repository is PUBLIC. The full ranked table across every company is the
   Pro product, so it cannot be committed -- the same reason
   `reports/output/*_pro.*` are gitignored.
2. Cloudflare rebuilds the site from the repository on every push, and that
   build has no `data/archive.db` (also gitignored). So anything the public
   site needs must exist in git in some form.
3. Free lookups are metered. A file the browser can fetch directly cannot be
   metered, so the per-ticker data must never be a publicly addressable asset.

The resolution:

  export_public()   archive -> ONE compact file, committed. Free-tier fields
                    only: the score, the verdict, the three components and a
                    few headline statistics. Not the full working.
  explode()         that file -> one small file per ticker, at build time,
                    written under a prefix that middleware blocks from direct
                    access. Only the tool Function reads them.
  export_pro()      archive -> per-ticker score history. Never committed;
                    written only by a local build, exactly like the Pro report.

Committing one compact file rather than 800 loose ones keeps the weekly diff
reviewable: you can see what changed in a single file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

import archive
from reports.data import _clean

SCHEMA_VERSION = 1

# What a free lookup shows. Deliberately not every column in the archive: the
# full working is what Pro buys, and a field published here cannot be taken
# back later without breaking the promise never to edit a published number.
FREE_FIELDS = [
    "score", "verdict", "performance", "consistency", "drawdown",
    "out_sample_sharpe_net", "max_drawdown_net", "pct_windows_positive",
    "num_trades", "cost_paid",
]

# The extra working Pro sees on top of FREE_FIELDS.
PRO_FIELDS = [
    "raw_score", "capped_by", "out_sample_sharpe_gross",
    "in_sample_sharpe_gross", "n_windows", "time_in_market",
]


def _pick(row: pd.Series, fields: List[str]) -> Dict[str, Any]:
    return _clean({f: row[f] for f in fields if f in row.index})


# --------------------------------------------------------------------------
# Step 1: archive -> one compact committed file
# --------------------------------------------------------------------------

def export_public(outfile: Path, week: Optional[int] = None,
                  path: Optional[Path] = None) -> Dict[str, Any]:
    """Write the free-tier lookup dataset as a single JSON file.

    This is the only score data that reaches the public repository, so it is
    also the only thing the deployed tool can serve to a free visitor.
    """
    week = week or archive.latest_week(path=path)
    if week is None:
        raise ValueError("no weeks recorded -- run run_scores.py first")

    table = archive.week_table(week, path=path)
    if table.empty:
        raise ValueError(f"week {week} is empty")

    published = str(table["published"].iloc[0])
    scores: Dict[str, Dict[str, Any]] = {}
    for _, row in table.iterrows():
        scores.setdefault(str(row["ticker"]), {})[str(row["indicator"])] = \
            _pick(row, FREE_FIELDS)

    payload = {
        "schema": SCHEMA_VERSION,
        "week": week,
        "published": published,
        "indicators": sorted(table["indicator"].unique()),
        "count": len(scores),
        "scores": scores,
    }
    outfile.parent.mkdir(parents=True, exist_ok=True)
    outfile.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True),
                       encoding="utf-8")
    return {"week": week, "published": published, "tickers": len(scores),
            "bytes": outfile.stat().st_size}


# --------------------------------------------------------------------------
# Step 2: compact file -> per-ticker files, at build time
# --------------------------------------------------------------------------

def explode(infile: Path, outdir: Path) -> Dict[str, Any]:
    """Split the committed dataset into one file per ticker.

    A lookup then costs one fetch of a few hundred bytes instead of parsing the
    whole universe, and Cloudflare caches each ticker at the edge separately.
    """
    payload = json.loads(infile.read_text(encoding="utf-8"))
    scores = payload["scores"]
    outdir.mkdir(parents=True, exist_ok=True)

    for ticker, indicators in scores.items():
        best = max(indicators, key=lambda k: indicators[k].get("score") or -1,
                   default=None)
        (outdir / f"{ticker}.json").write_text(
            json.dumps({
                "schema": SCHEMA_VERSION,
                "ticker": ticker,
                "week": payload["week"],
                "published": payload["published"],
                "indicators": indicators,
                "best_indicator": best,
            }, separators=(",", ":")), encoding="utf-8")

    # The index lets the page distinguish "we have not tested that company"
    # from "that is not a ticker" -- a difference worth being honest about.
    (outdir / "_index.json").write_text(json.dumps({
        "schema": SCHEMA_VERSION,
        "week": payload["week"],
        "published": payload["published"],
        "indicators": payload["indicators"],
        "count": len(scores),
        "tickers": sorted(scores),
    }, separators=(",", ":")), encoding="utf-8")

    return {"tickers": len(scores), "week": payload["week"]}


# --------------------------------------------------------------------------
# Step 3: Pro history. Local builds only -- never committed.
# --------------------------------------------------------------------------

def export_pro(outdir: Path, week: Optional[int] = None,
               path: Optional[Path] = None) -> Dict[str, Any]:
    """Per-ticker score history plus the full working, for Pro members.

    Answers "is this getting worse?", which one week cannot. Written straight
    to the members-only build output, exactly like the Pro report: it is
    generated locally and deployed, and never passes through git.
    """
    week = week or archive.latest_week(path=path)
    if week is None:
        raise ValueError("no weeks recorded")
    table = archive.week_table(week, path=path)
    indicators = sorted(table["indicator"].unique())
    outdir.mkdir(parents=True, exist_ok=True)

    written = 0
    for ticker in sorted(table["ticker"].unique()):
        rows = table[table["ticker"] == ticker]
        working = {str(r["indicator"]): _pick(r, FREE_FIELDS + PRO_FIELDS)
                   for _, r in rows.iterrows()}

        series: Dict[str, List[Dict[str, Any]]] = {}
        for indicator in indicators:
            frame = archive.history(indicator, ticker, path=path)
            if frame.empty:
                continue
            series[indicator] = [
                _clean({"week": int(r["week"]), "published": r["published"],
                        "score": r["score"], "verdict": r["verdict"],
                        "out_sample_sharpe_net": r["out_sample_sharpe_net"]})
                for _, r in frame.iterrows()
            ]

        (outdir / f"{ticker}.json").write_text(json.dumps({
            "schema": SCHEMA_VERSION,
            "ticker": ticker,
            "week": week,
            "indicators": working,
            "history": series,
            "weeks_recorded": max((len(v) for v in series.values()), default=0),
        }, separators=(",", ":")), encoding="utf-8")
        written += 1

    return {"tickers": written, "week": week}
