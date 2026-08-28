"""
Structured export of a week's results.

The markdown report is for humans reading on Substack or LinkedIn. This module
writes the same run as JSON, which is what the website builds from.

Why two formats rather than parsing the markdown back into data: parsing your
own prose is fragile, and it means a wording change in the report can silently
break the site. Emitting both from the same run keeps them in lockstep, and
the JSON is also the archive -- every published number stays queryable, so
"has RSI weakened since week 4?" is a question you can actually answer.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import REPORT_DIR, CostModel
from reports.weekly import VERDICT_MEANING, classify

SCHEMA_VERSION = 1


def _clean(value: Any) -> Any:
    """JSON has no NaN or Infinity. Convert them to null rather than emitting
    invalid JSON that only fails once a browser tries to parse it."""
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if hasattr(value, "item"):      # numpy scalar
        return _clean(value.item())
    return value


def build_payload(
    scores: Dict[str, Dict[str, float]],
    descriptions: Dict[str, str],
    benchmark: Dict[str, float],
    costs: CostModel,
    universe: List[str],
    period_start: str,
    period_end: str,
    split_label: str,
    test_start: str,
    test_end: str,
    walk_forward: Optional[Dict[str, Dict[str, float]]] = None,
    charts: Optional[Dict[str, Path]] = None,
    week_number: Optional[int] = None,
    markdown_file: Optional[str] = None,
    published: Optional[str] = None,
    reconstructed: bool = False,
) -> Dict[str, Any]:
    """Assemble one week's complete results as a JSON-serialisable dict."""
    bench_net = benchmark.get("out_sample_net")
    walk_forward = walk_forward or {}
    charts = charts or {}

    verdicts = {n: classify(s.get("out_sample_net", float("nan")), bench_net)
                for n, s in scores.items()}

    indicators = []
    for name, s in scores.items():
        indicators.append({
            "name": name,
            "description": descriptions.get(name, ""),
            "verdict": verdicts[name],
            "in_sample_gross": s.get("in_sample_gross"),
            "in_sample_net": s.get("in_sample_net"),
            "out_sample_gross": s.get("out_sample_gross"),
            "out_sample_net": s.get("out_sample_net"),
            "total_return_net": s.get("total_return_net"),
            "max_drawdown_net": s.get("max_drawdown_net"),
            "win_rate_net": s.get("win_rate_net"),
            "num_trades": s.get("num_trades"),
            "cost_paid": s.get("cost_paid"),
            "walk_forward": walk_forward.get(name, {}),
        })

    # Sort best-to-worst on the number that matters: net, out-of-sample.
    indicators.sort(key=lambda d: (d["out_sample_net"] is None,
                                   -(d["out_sample_net"] or 0.0)))

    survived = [i["name"] for i in indicators if i["verdict"] == "SURVIVED"]
    failed = [i["name"] for i in indicators if i["verdict"] == "FAILED"]

    payload = {
        "schema_version": SCHEMA_VERSION,
        "week": week_number,
        # The date this week was published, which is not always the date the
        # file was generated. A week rebuilt afterwards must carry the date it
        # actually went out, or the archive silently reorders itself and a
        # later week appears to precede an earlier one.
        "published": published or dt.date.today().isoformat(),
        # True when the report was generated after the fact from the data as
        # it stood at the time, rather than published on the day. The live
        # portfolio draws the same distinction, and for the same reason: a
        # reconstruction is honest, and pretending it was not is not.
        "reconstructed": bool(reconstructed),
        "period": {"start": period_start, "end": period_end},
        "out_of_sample": {"start": test_start, "end": test_end},
        "universe": list(universe),
        "universe_size": len(universe),
        "split_label": split_label,
        "costs": {
            "half_spread_bps": costs.half_spread_bps,
            "commission_bps": costs.commission_bps,
            "slippage_bps": costs.slippage_bps,
            "total_bps_per_side": costs.total_bps_per_side,
        },
        "benchmark": {
            "name": "Buy & hold",
            "in_sample_gross": benchmark.get("in_sample_gross"),
            "out_sample_gross": benchmark.get("out_sample_gross"),
            "out_sample_net": bench_net,
            "max_drawdown_net": benchmark.get("max_drawdown_net"),
            "total_return_net": benchmark.get("total_return_net"),
            "num_trades": benchmark.get("num_trades"),
            "cost_paid": benchmark.get("cost_paid"),
        },
        "indicators": indicators,
        "summary": {
            "tested": len(indicators),
            "survived": survived,
            "failed": failed,
            "any_survived": bool(survived),
        },
        "verdict_meanings": VERDICT_MEANING,
        "charts": {k: Path(v).name for k, v in charts.items()},
        "markdown_file": markdown_file,
    }
    return _clean(payload)


def write_payload(payload: Dict[str, Any], outfile: Optional[Path] = None) -> Path:
    """Write the JSON sidecar next to the markdown report."""
    if outfile is None:
        week = payload.get("week")
        stem = f"week{week:02d}" if week else payload["published"]
        outfile = REPORT_DIR / f"{stem}.json"

    outfile.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return outfile


def load_all(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Read every published week, newest first. Used by the site builder."""
    directory = directory or REPORT_DIR
    weeks = []
    for path in sorted(directory.glob("*.json")):
        try:
            weeks.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError as exc:
            print(f"  [skip] {path.name}: {exc}")

    weeks.sort(key=lambda w: (w.get("week") or 0, w.get("published", "")), reverse=True)
    return weeks
