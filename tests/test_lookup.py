"""
Checks on the lookup tool's data pipeline. Run with:

    python -m tests.test_lookup

Same style as tests/test_indicators.py: plain asserts, no framework. Each test
here exists because the mistake it catches would either publish paid data for
free or serve a stale number as if it were current.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pandas as pd

from reports import lookup

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def fake_week() -> pd.DataFrame:
    """One week's scores for two tickers, with every column the archive has."""
    rows = []
    for ticker, base in [("AAPL", 3.5), ("SHEL.L", 6.2)]:
        for i, indicator in enumerate(["EMA", "VWAP", "MACD", "RSI"]):
            rows.append({
                "week": 2, "published": "2026-08-24",
                "indicator": indicator, "ticker": ticker,
                "score": base + i * 0.1, "verdict": "Did not survive",
                "performance": 3.0, "consistency": 5.0, "drawdown": 2.0,
                "raw_score": base, "capped_by": None,
                "out_sample_sharpe_net": 0.1, "out_sample_sharpe_gross": 0.2,
                "in_sample_sharpe_gross": 0.4, "max_drawdown_net": -0.3,
                "pct_windows_positive": 0.5, "n_windows": 13,
                "num_trades": 40, "time_in_market": 0.9, "cost_paid": 0.05,
            })
    return pd.DataFrame(rows)


def write_public(tmp: Path) -> Path:
    """Build a public dataset from the fake week, without touching the archive."""
    table = fake_week()
    scores = {}
    for _, row in table.iterrows():
        scores.setdefault(row["ticker"], {})[row["indicator"]] = {
            f: (None if pd.isna(row[f]) else row[f]) for f in lookup.FREE_FIELDS
        }
    payload = {
        "schema": lookup.SCHEMA_VERSION, "week": 2, "published": "2026-08-24",
        "indicators": ["EMA", "MACD", "RSI", "VWAP"], "count": len(scores),
        "scores": scores,
    }
    out = tmp / "public_scores.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    return out


def test_public_dataset_withholds_the_pro_working():
    """The free file must not carry the columns Pro is sold on.

    Publishing a Pro field here would not just leak one number: the tool serves
    this file to anyone, so it would put the paid working on the open web, and
    the promise never to edit a published number means it could not be undone.
    """
    tmp = Path(tempfile.mkdtemp())
    infile = write_public(tmp)
    payload = json.loads(infile.read_text())
    leaked = set()
    for indicators in payload["scores"].values():
        for fields in indicators.values():
            leaked |= set(fields) & set(lookup.PRO_FIELDS)
    check("free dataset carries no Pro-only fields", not leaked,
          f"leaked: {sorted(leaked)}" if leaked else "")
    overlap = set(lookup.FREE_FIELDS) & set(lookup.PRO_FIELDS)
    check("free and Pro field lists do not overlap", not overlap,
          f"in both: {sorted(overlap)}" if overlap else "")


def test_explode_produces_one_file_per_ticker():
    tmp = Path(tempfile.mkdtemp())
    infile = write_public(tmp)
    outdir = tmp / "scores"
    result = lookup.explode(infile, outdir)

    check("every ticker got a file", result["tickers"] == 2,
          f"{result['tickers']} written")
    check("a dotted ticker keeps its symbol", (outdir / "SHEL.L.json").exists())

    data = json.loads((outdir / "AAPL.json").read_text())
    check("all four indicators survive the split",
          sorted(data["indicators"]) == ["EMA", "MACD", "RSI", "VWAP"],
          str(sorted(data["indicators"])))
    check("the published date rides along with the scores",
          data["published"] == "2026-08-24")


def test_best_indicator_is_actually_the_highest():
    """The page leads with this, so a wrong pick misreports the finding."""
    tmp = Path(tempfile.mkdtemp())
    outdir = tmp / "scores"
    lookup.explode(write_public(tmp), outdir)
    data = json.loads((outdir / "AAPL.json").read_text())
    scores = {k: v["score"] for k, v in data["indicators"].items()}
    check("best_indicator has the top score",
          scores[data["best_indicator"]] == max(scores.values()),
          f"{data['best_indicator']} in {scores}")


def test_index_lists_every_ticker():
    """The page tells 'not tested' apart from 'not a ticker' using this."""
    tmp = Path(tempfile.mkdtemp())
    outdir = tmp / "scores"
    lookup.explode(write_public(tmp), outdir)
    index = json.loads((outdir / "_index.json").read_text())
    check("index count matches the files written", index["count"] == 2)
    check("index lists both tickers",
          index["tickers"] == ["AAPL", "SHEL.L"], str(index["tickers"]))


def test_scores_are_json_safe():
    """NaN is not valid JSON. A single one makes a ticker's page fail to parse."""
    tmp = Path(tempfile.mkdtemp())
    outdir = tmp / "scores"
    lookup.explode(write_public(tmp), outdir)
    raw = (outdir / "AAPL.json").read_text()
    check("no NaN or Infinity in the output",
          "NaN" not in raw and "Infinity" not in raw)
    json.loads(raw)   # raises if malformed
    check("output parses as strict JSON", True)


def main() -> int:
    print("Margin & Co. — lookup tool checks\n")
    for fn in [
        test_public_dataset_withholds_the_pro_working,
        test_explode_produces_one_file_per_ticker,
        test_best_indicator_is_actually_the_highest,
        test_index_lists_every_ticker,
        test_scores_are_json_safe,
    ]:
        print(f"\n{fn.__name__}:")
        fn()

    print(f"\n{'-' * 60}")
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        for name in FAILED:
            print(f"  FAILED: {name}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
