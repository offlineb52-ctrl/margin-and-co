"""
Index constituent lists.

Pulls current S&P 500 and FTSE 350 membership from Wikipedia, caches it, and
falls back to a curated list if the fetch fails. Standard library only -- no
scraping framework, because the only thing being extracted is the first column
of one table.

THE BIAS THIS INTRODUCES, STATED PLAINLY
----------------------------------------
These are CURRENT constituents. A company that was in the index in 2012 and
has since been acquired, delisted, or dropped for poor performance does not
appear. Backtests run on this universe are therefore survivorship-biased
upward: we are only ever testing on companies that made it to today.

The correct fix is point-in-time membership data (CRSP, Norgate), which is
paid. Since this project cannot use it, the bias is declared in every report
instead of being quietly ignored -- and the headline result is deliberately
the IN-SAMPLE vs OUT-OF-SAMPLE comparison, which is affected far less because
both halves of the split share the same bias.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

from config import CACHE_DIR

WIKI = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "ftse100": "https://en.wikipedia.org/wiki/FTSE_100_Index",
    "ftse250": "https://en.wikipedia.org/wiki/FTSE_250_Index",
}

USER_AGENT = "Mozilla/5.0 (Margin & Co. research project; contact via repository)"

# Used when the network is unavailable, so a weekly run never fails outright.
FALLBACK_SP500 = [
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "BRK-B", "JPM", "V", "UNH",
    "XOM", "JNJ", "PG", "MA", "HD", "CVX", "MRK", "ABBV", "KO", "PEP",
    "AVGO", "COST", "WMT", "MCD", "CSCO", "ACN", "ADBE", "CRM", "TMO", "LIN",
]
FALLBACK_FTSE = [
    "SHEL.L", "AZN.L", "HSBA.L", "ULVR.L", "BP.L", "GSK.L", "RIO.L", "DGE.L",
    "BATS.L", "GLEN.L", "REL.L", "LSEG.L", "NG.L", "VOD.L", "BARC.L",
]


def _fetch(url: str, timeout: int = 30) -> str:
    ctx = ssl.create_default_context()
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout, context=ctx) as response:
        return response.read().decode("utf-8", "replace")


def _strip_tags(fragment: str) -> str:
    return re.sub(r"<[^>]+>", "", fragment).strip()


def _column_symbols(html: str, anchor: str, pattern: str, column: int = 0) -> List[str]:
    """Pull one cell from every row of the table following `anchor`.

    Wikipedia's rendered HTML puts ids on every element and wraps each symbol
    in an external link, so the reliable move is to take the cell, strip all
    markup, and validate the remaining text against a symbol pattern.

    The column index is not always zero: the S&P 500 page leads with the
    ticker, while the FTSE pages lead with the company name and put the EPIC
    second. Hard-coding column zero silently returned eight symbols instead of
    a hundred -- which is why `load()` treats a short result as a failure
    rather than trusting it.
    """
    index = html.find(anchor)
    if index < 0:
        return []

    table = html[index:]
    end = table.find("</table>")
    if end > 0:
        table = table[:end]

    symbols = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if not cells:
            continue
        if column >= len(cells):
            continue
        symbol = _strip_tags(cells[column]).replace("\u00a0", " ").strip()
        if re.fullmatch(pattern, symbol):
            symbols.append(symbol)

    # Preserve order, drop duplicates.
    seen, unique = set(), []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def _to_yahoo_us(symbol: str) -> str:
    """Wikipedia writes class shares as BRK.B; Yahoo wants BRK-B."""
    return symbol.replace(".", "-")


def _to_yahoo_lse(symbol: str) -> str:
    """London EPICs, in Yahoo's format.

        'AZN'   -> 'AZN.L'
        'BP.'   -> 'BP.L'     (trailing dot is Wikipedia's, not part of the code)
        'BT.A'  -> 'BT-A.L'   (share class separator is a dash on Yahoo)
    """
    code = symbol.rstrip(".")
    return code.replace(".", "-") + ".L"


def fetch_sp500() -> List[str]:
    html = _fetch(WIKI["sp500"])
    raw = _column_symbols(html, 'id="constituents"', r"[A-Z][A-Z\.\-]{0,6}", column=0)
    return [_to_yahoo_us(s) for s in raw]


def fetch_ftse350() -> List[str]:
    """FTSE 350 = FTSE 100 + FTSE 250, so two pages."""
    out: List[str] = []
    for key in ("ftse100", "ftse250"):
        try:
            html = _fetch(WIKI[key])
            # Company name is column 0 on these pages; the EPIC is column 1.
            raw = _column_symbols(html, 'id="Constituents"', r"[A-Z][A-Z\.]{0,5}\.?", column=1)
            if not raw:  # heading id casing varies between pages
                raw = _column_symbols(html, 'id="constituents"', r"[A-Z][A-Z\.]{0,5}\.?", column=1)
            out.extend(_to_yahoo_lse(s) for s in raw)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"  [warn] {key}: {exc}")

    seen, unique = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    return unique


def load(name: str, refresh: bool = False, verbose: bool = True) -> List[str]:
    """Return a named universe, using the on-disk cache unless `refresh`.

    Cached rather than fetched every run so that a week's results do not
    silently change because Wikipedia was edited between two runs.
    """
    path = CACHE_DIR / f"universe_{name}.json"

    if path.exists() and not refresh:
        tickers = json.loads(path.read_text())
        if verbose:
            print(f"  [cache] universe '{name}': {len(tickers)} tickers")
        return tickers

    fetchers = {"sp500": fetch_sp500, "ftse350": fetch_ftse350}
    fallbacks = {"sp500": FALLBACK_SP500, "ftse350": FALLBACK_FTSE}

    if name not in fetchers:
        raise ValueError(f"unknown universe '{name}'; expected one of {sorted(fetchers)}")

    try:
        tickers = fetchers[name]()
        if len(tickers) < 20:
            raise ValueError(f"only parsed {len(tickers)} tickers -- page layout may have changed")
        if verbose:
            print(f"  [fetch] universe '{name}': {len(tickers)} tickers from Wikipedia")
    except Exception as exc:  # noqa: BLE001 -- a weekly run must not die here
        print(f"  [warn] universe '{name}' fetch failed ({exc}); using fallback list")
        tickers = fallbacks[name]

    path.write_text(json.dumps(tickers, indent=1))
    return tickers
