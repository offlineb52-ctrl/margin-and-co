"""
Static site generator for Margin & Co.

Reads the JSON emitted by the research pipeline and writes a complete static
website to `site/dist/`. No framework, no build toolchain, no JavaScript --
just HTML, one stylesheet, and the charts the pipeline already produced.

    python site/build.py
    python site/build.py --serve      # build, then preview on localhost:8000

Why a hand-rolled generator rather than Hugo or Eleventy: the site has five
page types and its content comes from a Python pipeline that already exists.
Adding a Node toolchain would mean two dependency trees and a second language
to maintain, for a site that is essentially a table and a chart. This file is
under 500 lines and has no dependencies outside the standard library.

The output in `dist/` is plain files. Drop them on any static host --
Cloudflare Pages, GitHub Pages, Netlify -- and point a domain at it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

SITE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SITE_DIR.parent

# The site's settings module is named `siteconfig`, not `config`, because the
# research pipeline at the project root already owns the name `config` -- and
# whichever directory lands first on sys.path would silently win.
sys.path.insert(0, str(SITE_DIR))

import siteconfig as site_config  # noqa: E402

TEMPLATE_DIR = SITE_DIR / "templates"
CONTENT_DIR = SITE_DIR / "content"
STATIC_DIR = SITE_DIR / "static"
DIST = SITE_DIR / "dist"
REPORT_SOURCE = PROJECT_ROOT / "reports" / "output"
LIVE_STATE = PROJECT_ROOT / "live" / "state"


# --------------------------------------------------------------------------
# Minimal template engine
# --------------------------------------------------------------------------

TOKEN = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def render(template: str, **values: Any) -> str:
    """Replace every {{ name }} with the matching keyword argument.

    Deliberately strict: an unknown token raises rather than silently leaving
    `{{ foo }}` visible on a published page. A missing value is a bug you want
    to hear about at build time, not from a reader.
    """
    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in values:
            raise KeyError(f"template referenced unknown variable: {{{{ {key} }}}}")
        value = values[key]
        return "" if value is None else str(value)

    return TOKEN.sub(substitute, template)


def load_template(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Formatting helpers
#
# Every number on the site goes through one of these, so a Sharpe ratio is
# formatted identically on the home page, in a report, and in the archive.
# --------------------------------------------------------------------------

def num(value: Optional[float], places: int = 2, signed: bool = False) -> str:
    """Format a number, or an em-dash when it is missing."""
    if value is None:
        return "&mdash;"
    fmt = f"{{:{'+' if signed else ''}.{places}f}}"
    return fmt.format(value)


def pct(value: Optional[float], places: int = 1) -> str:
    if value is None:
        return "&mdash;"
    return f"{value * 100:.{places}f}%"


def signed_cell(value: Optional[float], places: int = 2) -> str:
    """A number wrapped in a class that colours it by sign.

    Red and green appear nowhere else on the site, so a coloured figure always
    means the same thing: this number's sign is the point.
    """
    if value is None:
        return '<span class="num num--nil">&mdash;</span>'
    cls = "num--pos" if value > 0 else ("num--neg" if value < 0 else "num--nil")
    return f'<span class="num {cls}">{value:.{places}f}</span>'


def signed_pct(value: Optional[float], places: int = 1) -> str:
    """A percentage coloured by sign, matching `signed_cell` for absolutes."""
    if value is None:
        return '<span class="num num--nil">&mdash;</span>'
    cls = "num--pos" if value > 0 else ("num--neg" if value < 0 else "num--nil")
    return f'<span class="num {cls}">{value * 100:+.{places}f}%</span>'


def pill(verdict: str) -> str:
    return f'<span class="pill pill--{verdict.lower()}">{html.escape(verdict)}</span>'


def esc(text: Any) -> str:
    return html.escape(str(text), quote=True)


TAGS = re.compile(r"<[^>]+>")


def plain_text(markup: str) -> str:
    """Strip tags and decode entities, yielding real text.

    Needed because the headline builders emit HTML for the page body, and the
    same sentence has to be reused in <title> and og:title as plain text.
    Without the unescape, titles render the literal string "&amp;" -- which is
    exactly what a link preview would show on LinkedIn.
    """
    return html.unescape(TAGS.sub("", markup)).strip()


def long_date(iso: str) -> str:
    """2026-08-23 -> 23 August 2026."""
    try:
        return dt.date.fromisoformat(iso).strftime("%-d %B %Y")
    except (ValueError, TypeError):
        return iso


def week_label(week: Optional[int]) -> str:
    return f"Week {week:02d}" if week else "Latest report"


def slug_for(week: Dict[str, Any]) -> str:
    return f"week-{week['week']:02d}" if week.get("week") else week.get("published", "report")


# --------------------------------------------------------------------------
# Narrative generation
#
# The site writes its own headline from the data. This matters: the wording
# cannot drift away from the numbers, because it is derived from them.
# --------------------------------------------------------------------------

def headline_for(week: Dict[str, Any]) -> str:
    survived = week["summary"]["survived"]
    tested = week["summary"]["tested"]

    if not survived:
        return f"None of the {tested} indicators beat buy &amp; hold once costs were applied."
    if len(survived) == 1:
        return f"{esc(survived[0])} was the only indicator to beat buy &amp; hold after costs."
    joined = ", ".join(esc(s) for s in survived[:-1]) + f" and {esc(survived[-1])}"
    return f"{joined} beat buy &amp; hold after costs."


def headline_detail(week: Dict[str, Any]) -> str:
    bench = week["benchmark"]["out_sample_net"]
    failed = week["summary"]["failed"]
    oos = week["out_of_sample"]

    parts = [
        f"Tested on {week['universe_size']} S&amp;P 500 and FTSE 350 companies, "
        f"measured over {long_date(oos['start'])} to {long_date(oos['end'])} — "
        f"data the rules had never seen."
    ]
    if bench is not None:
        parts.append(f"Buy &amp; hold returned a Sharpe ratio of {num(bench)} over the same window.")
    if failed:
        names = ", ".join(esc(f) for f in failed)
        parts.append(f"{names} produced a negative Sharpe after costs — "
                     f"they lost money net of what it cost to trade them.")
    return " ".join(parts)


def worst_cost(week: Dict[str, Any]) -> Dict[str, Any]:
    """The indicator that paid away the most in trading costs."""
    ranked = [i for i in week["indicators"] if i.get("cost_paid") is not None]
    if not ranked:
        return {"name": "&mdash;", "cost_paid": None}
    return max(ranked, key=lambda i: i["cost_paid"])


# --------------------------------------------------------------------------
# Component builders
# --------------------------------------------------------------------------

def results_table(week: Dict[str, Any]) -> str:
    rows = []
    for ind in week["indicators"]:
        rows.append(f"""      <tr>
        <td>{esc(ind['name'])}</td>
        <td>{signed_cell(ind['in_sample_gross'])}</td>
        <td>{signed_cell(ind['out_sample_gross'])}</td>
        <td>{signed_cell(ind['out_sample_net'])}</td>
        <td class="num">{num(ind['num_trades'], 0)}</td>
        <td class="num">{pct(ind['cost_paid'])}</td>
        <td class="num">{pct(ind['max_drawdown_net'])}</td>
        <td>{pill(ind['verdict'])}</td>
      </tr>""")

    b = week["benchmark"]
    rows.append(f"""      <tr class="is-benchmark">
        <td>Buy &amp; hold</td>
        <td>{signed_cell(b['in_sample_gross'])}</td>
        <td>{signed_cell(b['out_sample_gross'])}</td>
        <td>{signed_cell(b['out_sample_net'])}</td>
        <td class="num">{num(b['num_trades'], 0)}</td>
        <td class="num">{pct(b['cost_paid'])}</td>
        <td class="num">{pct(b['max_drawdown_net'])}</td>
        <td>benchmark</td>
      </tr>""")

    return f"""<div class="table-scroll">
  <table>
    <caption class="visually-hidden">Indicator performance, {week_label(week.get('week'))}</caption>
    <thead>
      <tr>
        <th scope="col">Indicator</th>
        <th scope="col">In-sample<br>Sharpe</th>
        <th scope="col">Out-of-sample<br>Sharpe (gross)</th>
        <th scope="col">Out-of-sample<br>Sharpe (net)</th>
        <th scope="col">Trades</th>
        <th scope="col">Cost paid</th>
        <th scope="col">Max drawdown</th>
        <th scope="col">Verdict</th>
      </tr>
    </thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
</div>"""


def findings_list(week: Dict[str, Any]) -> str:
    cards = []
    for ind in week["indicators"]:
        cards.append(f"""  <article class="finding">
    <div class="finding__head">
      <h3 class="finding__name">{esc(ind['name'])}</h3>
      {pill(ind['verdict'])}
    </div>
    <p class="finding__rule">{esc(ind['description'])}</p>
    <p class="finding__body">Posted a Sharpe ratio of
      <span class="num">{num(ind['in_sample_gross'])}</span> in-sample,
      <span class="num">{num(ind['out_sample_gross'])}</span> on data it had
      never seen, and <span class="num">{num(ind['out_sample_net'])}</span>
      once trading costs were applied — across
      <span class="num">{num(ind['num_trades'], 0)}</span> trades that paid
      away <span class="num">{pct(ind['cost_paid'])}</span> of notional.</p>
  </article>""")
    return f'<div class="findings">\n{chr(10).join(cards)}\n</div>'


def walk_forward_table(week: Dict[str, Any]) -> str:
    rows = []
    for ind in week["indicators"]:
        wf = ind.get("walk_forward") or {}
        if not wf.get("n_windows"):
            continue
        rows.append(f"""      <tr>
        <td>{esc(ind['name'])}</td>
        <td class="num">{num(wf.get('n_windows'), 0)}</td>
        <td>{signed_cell(wf.get('mean_sharpe_net'))}</td>
        <td class="num">{pct(wf.get('pct_positive'), 0)}</td>
        <td>{signed_cell(wf.get('worst_sharpe_net'))}</td>
      </tr>""")

    if not rows:
        return ""

    return f"""<section class="wrap section section--ruled">
  <div class="section__head">
    <p class="eyebrow">Walk-forward check</p>
    <h2>Did it hold up repeatedly, or once?</h2>
  </div>
  <div class="prose">
    <p>A single 70/30 split gives one reading, and one reading can be luck.
       Each indicator is re-tested across rolling three-year training windows
       with the following year held out, giving many independent
       out-of-sample readings instead of one.</p>
    <p>The column that matters is not the mean — it is how often the
       indicator was positive. A strategy averaging 0.4 on the back of one
       spectacular year and four flat ones is a very different proposition
       from one that earns 0.4 every year.</p>
  </div>
  <div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th scope="col">Indicator</th>
          <th scope="col">Windows</th>
          <th scope="col">Mean Sharpe (net)</th>
          <th scope="col">Windows positive</th>
          <th scope="col">Worst window</th>
        </tr>
      </thead>
      <tbody>
{chr(10).join(rows)}
      </tbody>
    </table>
  </div>
</section>"""


def dark_variant(src: str) -> str:
    """foo.png -> foo_dark.png. The pipeline renders both."""
    return src[:-4] + "_dark.png" if src.endswith(".png") else src


def figure(src: str, alt: str, caption: str) -> str:
    """A themed figure.

    <picture> lets the browser pick the dark rendering before it paints, so
    there is no flash of a white chart on a dark page and no CSS filter
    distorting the colours. The <img> stays the fallback for any browser that
    ignores the source, and it is what social scrapers read.
    """
    if not src:
        return ""
    return f"""<figure>
  <picture>
    <source srcset="{esc(dark_variant(src))}" media="(prefers-color-scheme: dark)">
    <img src="{esc(src)}" alt="{esc(alt)}" loading="lazy" decoding="async">
  </picture>
  <figcaption>{caption}</figcaption>
</figure>"""


# --------------------------------------------------------------------------
# Live portfolio page
# --------------------------------------------------------------------------

def pending_table(orders: List[Dict[str, Any]]) -> str:
    """Orders committed but not yet filled -- the page's integrity claim."""
    if not orders:
        return ('<div class="note"><p>No orders standing. The strategy is '
                'holding its current book into the next session — which for a '
                'mean-reversion rule that spends most of its time in cash is '
                'the normal state, not an omission.</p></div>')

    rows = []
    for o in orders:
        side_class = "num--pos" if o["side"] == "BUY" else "num--neg"
        rsi = o.get("signal_value")
        rows.append(f"""      <tr>
        <td>{esc(o['ticker'])}</td>
        <td><span class="num {side_class}">{esc(o['side'])}</span></td>
        <td class="num">{num(o['shares'], 0)}</td>
        <td class="num">{num(rsi, 1) if rsi is not None else '&mdash;'}</td>
        <td class="reason">{esc(o.get('reason', ''))}</td>
        <td class="num">{esc(o['decided_on'])}</td>
      </tr>""")

    return f"""<div class="table-scroll">
  <table>
    <thead><tr>
      <th scope="col">Ticker</th><th scope="col">Side</th><th scope="col">Shares</th>
      <th scope="col">RSI</th><th scope="col">Reason</th><th scope="col">Decided</th>
    </tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
</div>"""


def positions_table(positions: List[Dict[str, Any]], currency: str) -> str:
    if not positions:
        return ('<div class="note"><p>The book is entirely in cash. This '
                'strategy only holds a name while it is oversold, so flat is a '
                'position too.</p></div>')

    rows = []
    for p in positions:
        rows.append(f"""      <tr>
        <td>{esc(p['ticker'])}</td>
        <td class="num">{num(p['shares'], 0)}</td>
        <td class="num">{num(p['cost_basis'])}</td>
        <td class="num">{num(p['value_at_cost'], 0)}</td>
        <td class="num">{esc(p['opened_on'])}</td>
      </tr>""")

    return f"""<div class="table-scroll">
  <table>
    <thead><tr>
      <th scope="col">Ticker</th><th scope="col">Shares</th>
      <th scope="col">Avg entry ({esc(currency)})</th>
      <th scope="col">Cost ({esc(currency)})</th>
      <th scope="col">Opened</th>
    </tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
</div>"""


def trades_table(trades: List[Dict[str, Any]]) -> str:
    if not trades:
        return '<div class="note"><p>No completed round trips yet.</p></div>'

    rows = []
    for t in trades:
        flag = ('<span class="pill pill--weakened">reconstructed</span>'
                if t.get("backfilled") else '<span class="pill pill--survived">live</span>')
        rows.append(f"""      <tr>
        <td>{esc(t['ticker'])}</td>
        <td class="num">{esc(t['opened_on'])}</td>
        <td class="num">{esc(t['closed_on'])}</td>
        <td class="num">{num(t['avg_entry'])}</td>
        <td class="num">{num(t['avg_exit'])}</td>
        <td>{signed_cell(t['pnl'], 0)}</td>
        <td>{signed_pct(t['pnl_pct'])}</td>
        <td class="reason">{esc(t['close_reason'])}</td>
        <td>{flag}</td>
      </tr>""")

    return f"""<div class="table-scroll">
  <table>
    <thead><tr>
      <th scope="col">Ticker</th><th scope="col">Opened</th><th scope="col">Closed</th>
      <th scope="col">Entry</th><th scope="col">Exit</th>
      <th scope="col">P&amp;L</th><th scope="col">Return</th>
      <th scope="col">Why it closed</th><th scope="col">Record</th>
    </tr></thead>
    <tbody>
{chr(10).join(rows)}
    </tbody>
  </table>
</div>"""


def archive_list(weeks: List[Dict[str, Any]], root: str) -> str:
    items = []
    for w in weeks:
        slug = slug_for(w)
        survived = w["summary"]["survived"]
        verdict = (f"{len(survived)} of {w['summary']['tested']} beat the benchmark"
                   if survived else "No indicator beat the benchmark")
        items.append(f"""  <article class="archive__item">
    <div class="archive__week">{esc(week_label(w.get('week')))}</div>
    <h3 class="archive__title"><a href="{root}reports/{slug}/">{headline_for(w)}</a></h3>
    <div class="archive__meta">{esc(long_date(w['published']))}</div>
    <p class="archive__summary">{verdict}. {w['universe_size']} companies,
       out-of-sample {esc(long_date(w['out_of_sample']['start']))} to
       {esc(long_date(w['out_of_sample']['end']))}, costed at
       {num(w['costs']['total_bps_per_side'], 0)}bps per side.</p>
  </article>""")
    return f'<div class="archive">\n{chr(10).join(items)}\n</div>'


# --------------------------------------------------------------------------
# Page assembly
# --------------------------------------------------------------------------

class Builder:
    def __init__(self) -> None:
        self.base = load_template("base.html")
        self.root = site_config.BASE_PATH
        self.domain = site_config.DOMAIN.rstrip("/")
        self.build_date = dt.date.today()
        self.pages: List[str] = []   # relative URLs, for the sitemap

    def shell(
        self,
        content: str,
        *,
        path: str,
        title: str,
        description: str,
        og_title: Optional[str] = None,
        og_image: str = "og-default.png",
        og_type: str = "website",
        nav: str = "",
        head_extra: str = "",
    ) -> str:
        """Wrap page content in the site shell, with correct metadata."""
        current = ' aria-current="page"'
        canonical = f"{self.domain}{self.root}{path}"

        self.pages.append(f"{self.root}{path}")

        full_title = (title if title == site_config.SITE_NAME
                      else f"{title} — {site_config.SITE_NAME}")

        # Every caller passes PLAIN TEXT; escaping happens here, once, so a
        # value can never be double-escaped or left raw.
        return render(
            self.base,
            page_title=esc(full_title),
            description=esc(description),
            canonical=canonical,
            author=esc(site_config.AUTHOR),
            site_name=esc(site_config.SITE_NAME),
            og_title=esc(og_title or title),
            og_type=og_type,
            og_image=f"{self.domain}{self.root}{og_image}",
            root=self.root,
            repo_url=site_config.REPO_URL,
            year=self.build_date.year,
            build_date=self.build_date.strftime("%-d %B %Y"),
            content=content,
            head_extra=head_extra,
            nav_home=current if nav == "home" else "",
            nav_live=current if nav == "live" else "",
            nav_reports=current if nav == "reports" else "",
            nav_method=current if nav == "method" else "",
            nav_about=current if nav == "about" else "",
        )

    # -- individual pages ----------------------------------------------

    def build_index(self, weeks: List[Dict[str, Any]]) -> str:
        latest = weeks[0]
        wc = worst_cost(latest)
        charts = latest.get("charts", {})

        archive_section = ""
        if len(weeks) > 1:
            archive_section = f"""<section class="wrap section section--ruled">
  <div class="section__head">
    <p class="eyebrow">Archive</p>
    <h2>Previous weeks</h2>
  </div>
  {archive_list(weeks[1:6], self.root)}
  <p class="btn-row"><a class="btn" href="{self.root}reports/">All reports</a></p>
</section>"""

        content = render(
            load_template("index.html"),
            root=self.root,
            week_label=week_label(latest.get("week")),
            week_label_lower=week_label(latest.get("week")).lower(),
            published_long=long_date(latest["published"]),
            headline=headline_for(latest),
            headline_detail=headline_detail(latest),
            stat_tested=latest["summary"]["tested"],
            stat_survived=len(latest["summary"]["survived"]),
            stat_benchmark=num(latest["benchmark"]["out_sample_net"]),
            stat_worst_cost=pct(wc.get("cost_paid"), 0),
            stat_worst_cost_name=esc(wc.get("name", "")),
            universe_line=self.universe_line(latest),
            decay_figure=figure(
                f"reports/{slug_for(latest)}/{charts.get('decay', '')}",
                "Sharpe ratio by indicator: in-sample, out-of-sample, and net of costs",
                "Left bar: what the indicator looked like on the data used to design it. "
                "Middle: the same rule on data it had never seen. Right: after paying to "
                "trade. The gap between the first and last bar is the number retail "
                "backtests do not show you."),
            results_table=results_table(latest),
            latest_url=f"{self.root}reports/{slug_for(latest)}/",
            archive_section=archive_section,
        )
        return self.shell(
            content, path="", title=site_config.SITE_NAME,
            description=site_config.DESCRIPTION,
            og_title=f"{site_config.SITE_NAME} — {site_config.TAGLINE}",
            og_image=f"reports/{slug_for(latest)}/{charts.get('decay', '')}",
            nav="home")

    def universe_line(self, week: Dict[str, Any]) -> str:
        names = week["universe"]
        shown = ", ".join(names[:6])
        more = f" and {len(names) - 6} others" if len(names) > 6 else ""
        return (f"{week['universe_size']} companies — {shown}{more} — over "
                f"{long_date(week['period']['start'])} to {long_date(week['period']['end'])}, "
                f"costed at {num(week['costs']['total_bps_per_side'], 0)} basis points per side.")

    def build_report(self, week: Dict[str, Any]) -> str:
        slug = slug_for(week)
        charts = week.get("charts", {})
        oos = week["out_of_sample"]
        years = (dt.date.fromisoformat(oos["end"]) - dt.date.fromisoformat(oos["start"])).days / 365.25

        equity_section = ""
        if charts.get("equity"):
            equity_section = f"""<section class="wrap section section--ruled">
  <div class="section__head">
    <p class="eyebrow">Cumulative</p>
    <h2>Growth of 1.00, net of costs</h2>
  </div>
  {figure(charts['equity'], 'Equity curves for each indicator against buy and hold',
          'Equal-weighted across the universe, log scale. The benchmark is the grey dashed line.')}
</section>"""

        sensitivity_section = ""
        if charts.get("sensitivity"):
            sensitivity_section = f"""<section class="wrap section section--ruled">
  <div class="section__head">
    <p class="eyebrow">Sensitivity</p>
    <h2>How wrong could the cost assumption be?</h2>
  </div>
  <div class="prose">
    <p>The obvious objection to any cost-adjusted backtest is that the cost
       number was chosen by the author. Showing the whole curve is a better
       answer than defending one point on it. Where each line crosses zero is
       the highest trading cost that indicator could tolerate and still break
       even.</p>
  </div>
  {figure(charts['sensitivity'], 'Out-of-sample net Sharpe as the cost assumption rises',
          'Out-of-sample Sharpe, net, as the assumed cost rises from zero to 40 basis points per side.')}
</section>"""

        content = render(
            load_template("report.html"),
            week_label=week_label(week.get("week")),
            week_number=week.get("week") or 1,
            published_long=long_date(week["published"]),
            headline=headline_for(week),
            headline_detail=headline_detail(week),
            stat_universe=week["universe_size"],
            stat_oos_years=f"{years:.1f} yrs",
            oos_window=f"{long_date(oos['start'])} – {long_date(oos['end'])}",
            stat_costs=num(week["costs"]["total_bps_per_side"], 0),
            stat_benchmark=num(week["benchmark"]["out_sample_net"]),
            universe_line=self.universe_line(week),
            decay_figure=figure(
                charts.get("decay", ""),
                "Sharpe ratio by indicator: in-sample, out-of-sample, and net of costs",
                "Three readings per indicator. A genuinely useful signal shows three "
                "similar bars; the usual result is a tall first bar and a third bar at "
                "or below zero."),
            results_table=results_table(week),
            findings=findings_list(week),
            walk_forward_section=walk_forward_table(week),
            equity_section=equity_section,
            sensitivity_section=sensitivity_section,
            markdown_url=week.get("markdown_file", "#"),
            json_url=f"week{week['week']:02d}.json" if week.get("week") else "#",
        )

        plain = plain_text(headline_for(week))
        return self.shell(
            content, path=f"reports/{slug}/", title=f"{week_label(week.get('week'))}: {plain}",
            description=plain_text(headline_detail(week))[:300],
            og_title=plain, og_type="article",
            og_image=f"reports/{slug}/{charts.get('decay', '')}",
            nav="reports")

    def build_live(self, live: Dict[str, Any]) -> str:
        strat, book, perf = live["strategy"], live["book"], live["performance"]
        currency = book["currency"]

        backfilled = live.get("backfilled_sessions", 0)
        live_since = live.get("live_since")
        if live_since:
            provenance = (
                f"The first {backfilled} sessions, up to {long_date(live_since)}, were "
                f"reconstructed when the book was opened on {long_date(book['opened'])}. "
                f"They ran through the identical daily sequence — decide on the close, "
                f"fill at the next open — so no future information touched any decision, "
                f"but they were not published in advance. Everything from "
                f"{long_date(live_since)} onward was recorded forward, one day at a time, "
                f"and cannot be recomputed.")
        else:
            provenance = (
                f"Every session shown so far was reconstructed when this book was opened "
                f"on {long_date(book['opened'])}, running back to {long_date(book['inception'])}. "
                f"Each ran through the identical daily sequence — decide on the close, fill "
                f"at the next open — so no future information touched any decision. But none "
                f"of it was published in advance, so treat it as a backtest until forward "
                f"recording accumulates. Days from here are appended one at a time and are "
                f"never recomputed.")

        invested = perf.get("avg_invested") or 0.0
        excess = perf.get("excess_return")
        if excess is not None and invested:
            direction = "ahead of" if excess > 0 else "behind"
            exposure_note = (
                f"<strong>Read the exposure figure alongside the return.</strong> "
                f"The portfolio finished {pct(abs(excess))} {direction} buy &amp; hold "
                f"while holding an average of just {pct(invested, 0)} of the book in the "
                f"market. Matching a fully-invested benchmark from a third of the exposure "
                f"is a different result from matching it fully invested — better on a "
                f"risk-adjusted basis, and worse if what you want is compounding.")
        else:
            exposure_note = ("The strategy holds cash whenever no name is oversold, so its "
                             "return is not directly comparable to a fully-invested benchmark.")

        trades = live.get("closed_trades_list", [])
        wins = sum(1 for t in trades if (t.get("pnl") or 0) > 0)
        trades_summary = (
            f"{perf.get('closed_trades', 0)} completed round trips, "
            f"{wins} of the {len(trades)} shown profitable. "
            f"{currency} {perf.get('total_costs_paid', 0):,.2f} paid in trading costs "
            f"across the life of the book."
        ) if trades else "No completed round trips yet."

        rules = "\n".join(f"    <li>{esc(r)}</li>" for r in strat["rules"])

        names = strat["universe"]
        universe_line = (
            f"{strat['universe_size']} US large caps, fixed at inception and never "
            f"changed: {', '.join(esc(n) for n in names)}. US only — FTSE names are "
            f"quoted in pence, and mixing currencies without an FX model would make "
            f"the portfolio value wrong.")

        content = render(
            load_template("live.html"),
            strategy_name=esc(strat["name"]),
            rationale=esc(strat["rationale"]),
            provenance=provenance,
            costs_bps=num(strat["costs_bps_per_side"], 0),
            stat_nav=f"{perf['nav']:,.0f}" if perf.get("nav") else "&mdash;",
            stat_capital=f"{currency} {book['starting_capital']:,.0f}",
            stat_return=signed_pct(perf.get("total_return")),
            stat_sessions=book["sessions"],
            stat_bench=signed_pct(perf.get("benchmark_return")),
            stat_excess=signed_pct(perf.get("excess_return")),
            stat_invested=pct(perf.get("avg_invested"), 0),
            exposure_note=exposure_note,
            equity_figure=figure(
                "live_equity.png",
                "Live paper portfolio value against an equal-weight buy and hold benchmark",
                "Blue: the strategy. Grey dashed: buy &amp; hold of the same names, bought "
                "at inception and left alone. The faint band along the bottom is the share "
                "of the book actually invested — the strategy sits in cash whenever nothing "
                "is oversold."),
            pending_table=pending_table(live.get("pending_orders", [])),
            positions_table=positions_table(live.get("positions", []), currency),
            trades_summary=trades_summary,
            trades_table=trades_table(trades),
            rules_list=rules,
            universe_line=universe_line,
            ledger_url="ledger.json",
            equity_url="equity.json",
            live_json_url="live.json",
        )

        return self.shell(
            content, path="live/",
            title="Live paper portfolio",
            description=(f"A public paper portfolio trading {strat['name']}, with every "
                         f"order committed before it fills and the full ledger published."),
            og_title=f"Live paper portfolio — {strat['name']}",
            og_image="live/live_equity.png",
            nav="live")


    def build_archive(self, weeks: List[Dict[str, Any]]) -> str:
        content = render(load_template("archive.html"),
                         archive_list=archive_list(weeks, self.root))
        return self.shell(content, path="reports/", title="Archive",
                          description="Every Margin & Co. report, from the first.",
                          nav="reports")

    def build_content_page(self, name: str, nav: str) -> str:
        """Render a hand-written HTML page from site/content/."""
        raw = (CONTENT_DIR / f"{name}.html").read_text(encoding="utf-8")
        meta, body = raw.split("<!--/meta-->", 1)
        fields = dict(re.findall(r"(\w+):\s*(.+)", meta))

        content = render(load_template("page.html"),
                         eyebrow=fields.get("eyebrow", ""),
                         heading=fields.get("heading", name.title()),
                         lede=fields.get("lede", ""),
                         body=body.strip())
        return self.shell(content, path=f"{name}/",
                          title=fields.get("title", name.title()),
                          description=fields.get("description", site_config.DESCRIPTION),
                          nav=nav)


# --------------------------------------------------------------------------
# Assets and site-wide files
# --------------------------------------------------------------------------

FAVICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" fill="#0B4F9C"/>
  <rect x="12" y="30" width="8" height="22" fill="#FCFBF9"/>
  <rect x="28" y="20" width="8" height="32" fill="#FCFBF9"/>
  <rect x="44" y="38" width="8" height="14" fill="#9ECAE1"/>
</svg>"""


def write_sitemap(pages: List[str], domain: str, outfile: Path) -> None:
    today = dt.date.today().isoformat()
    urls = "\n".join(
        f"  <url><loc>{domain}{p}</loc><lastmod>{today}</lastmod></url>"
        for p in sorted(set(pages))
    )
    outfile.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n</urlset>\n", encoding="utf-8")


def copy_report_assets(weeks: List[Dict[str, Any]]) -> None:
    """Copy each week's charts, markdown, and JSON into its own directory.

    Assets live beside the page that uses them, so a report URL is
    self-contained and nothing breaks when an old week is re-published.
    """
    for week in weeks:
        target = DIST / "reports" / slug_for(week)
        target.mkdir(parents=True, exist_ok=True)

        for filename in week.get("charts", {}).values():
            for name in (filename, dark_variant(filename)):
                src = REPORT_SOURCE / name
                if src.exists():
                    shutil.copy2(src, target / name)

        for filename in (week.get("markdown_file"),
                         f"week{week['week']:02d}.json" if week.get("week") else None):
            if filename and (REPORT_SOURCE / filename).exists():
                shutil.copy2(REPORT_SOURCE / filename, target / filename)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Margin & Co. website")
    parser.add_argument("--serve", action="store_true", help="serve dist/ after building")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    print("Building Margin & Co. ...")

    weeks = []
    for path in sorted(REPORT_SOURCE.glob("week*.json")):
        weeks.append(json.loads(path.read_text(encoding="utf-8")))
    weeks.sort(key=lambda w: (w.get("week") or 0), reverse=True)

    if not weeks:
        print(f"ERROR: no report JSON found in {REPORT_SOURCE}.\n"
              f"Run `python run_pipeline.py --universe all --week 1` first.", file=sys.stderr)
        return 1

    print(f"  {len(weeks)} report(s) found: "
          f"{', '.join(week_label(w.get('week')) for w in weeks)}")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)

    builder = Builder()

    (DIST / "index.html").write_text(builder.build_index(weeks), encoding="utf-8")
    print("  index.html")

    (DIST / "reports").mkdir(exist_ok=True)
    (DIST / "reports" / "index.html").write_text(builder.build_archive(weeks), encoding="utf-8")
    print("  reports/index.html")

    for week in weeks:
        target = DIST / "reports" / slug_for(week)
        target.mkdir(parents=True, exist_ok=True)
        (target / "index.html").write_text(builder.build_report(week), encoding="utf-8")
        print(f"  reports/{slug_for(week)}/index.html")

    # Live portfolio page, if a book has been opened.
    live_file = LIVE_STATE / "live.json"
    if live_file.exists():
        live = json.loads(live_file.read_text(encoding="utf-8"))
        target = DIST / "live"
        target.mkdir(exist_ok=True)
        (target / "index.html").write_text(builder.build_live(live), encoding="utf-8")

        for name in ("live_equity.png", "live_equity_dark.png"):
            src = REPORT_SOURCE / name
            if src.exists():
                shutil.copy2(src, target / name)

        # Publish the raw book so the page can be audited, not just read.
        for name in ("ledger.json", "equity.json", "live.json", "meta.json"):
            src = LIVE_STATE / name
            if src.exists():
                shutil.copy2(src, target / name)
        print("  live/index.html")
    else:
        print("  (no live portfolio yet -- skipping live/)")

    for name, nav in (("methodology", "method"), ("about", "about")):
        target = DIST / name
        target.mkdir(exist_ok=True)
        (target / "index.html").write_text(builder.build_content_page(name, nav), encoding="utf-8")
        print(f"  {name}/index.html")

    shutil.copytree(STATIC_DIR, DIST, dirs_exist_ok=True)
    copy_report_assets(weeks)

    (DIST / "favicon.svg").write_text(FAVICON, encoding="utf-8")
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {builder.domain}{builder.root}sitemap.xml\n",
        encoding="utf-8")
    write_sitemap(builder.pages, builder.domain, DIST / "sitemap.xml")

    # GitHub Pages reads CNAME to attach a custom domain. Cloudflare Pages and
    # Netlify ignore the file, so writing it always is harmless.
    bare_domain = builder.domain.replace("https://", "").replace("http://", "")
    if bare_domain and "example" not in bare_domain:
        (DIST / "CNAME").write_text(bare_domain + "\n", encoding="utf-8")

    # Cloudflare Pages and Netlify both read _headers for cache policy.
    # Security headers. Cloudflare Pages reads _headers at deploy time.
    #
    # The strongest line here is `script-src 'none'`: this site ships no
    # JavaScript at all, so the browser is told to refuse any that appears.
    # That makes cross-site scripting — the most common web vulnerability —
    # structurally impossible rather than merely unlikely. The only external
    # origins allowed are Google Fonts, and nothing may frame the site.
    csp = "; ".join([
        "default-src 'self'",
        "script-src 'none'",
        "style-src 'self' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "upgrade-insecure-requests",
    ])

    (DIST / "_headers").write_text(
        "/css/*\n  Cache-Control: public, max-age=31536000, immutable\n"
        "/reports/*.png\n  Cache-Control: public, max-age=31536000, immutable\n"
        "/*\n"
        "  X-Content-Type-Options: nosniff\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "  X-Frame-Options: DENY\n"
        "  Strict-Transport-Security: max-age=31536000; includeSubDomains; preload\n"
        "  Permissions-Policy: geolocation=(), microphone=(), camera=(), payment=()\n"
        "  Cross-Origin-Opener-Policy: same-origin\n"
        f"  Content-Security-Policy: {csp}\n", encoding="utf-8")

    files = sum(1 for _ in DIST.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file()) / 1024
    print(f"\nBuilt {files} files ({size:.0f} KB) -> {DIST}")

    if args.serve:
        import http.server, socketserver, os
        os.chdir(DIST)
        print(f"\nServing on http://localhost:{args.port}  (ctrl-C to stop)")
        with socketserver.TCPServer(("", args.port), http.server.SimpleHTTPRequestHandler) as srv:
            srv.serve_forever()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
