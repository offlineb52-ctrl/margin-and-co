"""
Download and cache the full S&P 500 + FTSE 350 universe.

Run this once before a full-universe pipeline run. It is slow (Yahoo is rate
limited and there are ~850 symbols), so it is a separate step rather than
something the weekly run does implicitly. Once cached, later runs are instant.

    python warm_cache.py            # everything
    python warm_cache.py --limit 50 # a quick subset, for testing
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import List

from data import universe
from data.loader import load_ticker


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--refresh", action="store_true", help="re-download cached tickers")
    args = parser.parse_args(argv)

    tickers: List[str] = universe.load("sp500") + universe.load("ftse350")
    if args.limit:
        tickers = tickers[: args.limit]

    print(f"Warming cache for {len(tickers)} tickers\n", flush=True)
    started = time.time()
    ok, failed = 0, []

    for i, ticker in enumerate(tickers, 1):
        try:
            df = load_ticker(ticker, use_cache=not args.refresh, verbose=False)
            ok += 1
            if i % 25 == 0 or i == len(tickers):
                rate = i / max(time.time() - started, 1)
                remaining = (len(tickers) - i) / max(rate, 0.01)
                print(f"  {i:4d}/{len(tickers)}  ok={ok} failed={len(failed)}  "
                      f"{rate:.1f}/s  ~{remaining/60:.1f}min left", flush=True)
        except Exception as exc:  # noqa: BLE001
            failed.append((ticker, str(exc)[:60]))

    print(f"\nDone in {(time.time() - started)/60:.1f} min: "
          f"{ok} cached, {len(failed)} failed", flush=True)
    if failed:
        print("Failed symbols (usually delisted or renamed):", flush=True)
        for ticker, reason in failed[:40]:
            print(f"  {ticker}: {reason}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
