# Margin & Co.

**Do the trading indicators that retail traders rely on actually make money once you account for what it costs to trade them?**

This is an independent research project that tests four of the most widely used technical indicators — EMA, VWAP, MACD, and RSI — under conditions designed to be hard to fool. It publishes the results weekly, including the weeks the answer is "no".

---

## Why this project exists

Search for "MACD strategy backtest" and you will find thousands of results showing spectacular returns. Almost all of them share three flaws:

1. **They test on the same data used to choose the settings.** Try enough combinations of moving-average lengths and one of them will have worked, by chance. That is a search result, not a discovery.
2. **They ignore trading costs.** Every trade pays the bid-ask spread, commission, and slippage. A strategy that trades daily pays those costs roughly 250 times a year.
3. **They accidentally use tomorrow's information.** A signal computed from Monday's closing price cannot be traded until Monday has closed — but most homemade backtests let it earn Monday's return anyway. This one mistake typically adds one to three points of Sharpe ratio out of thin air.

This project controls for all three, and publishes what is left.

## What it does

- Downloads daily, split- and dividend-adjusted price data for S&P 500 and FTSE 350 companies.
- Runs each indicator over the history, producing a target position each day: long, short, or flat.
- Splits the history in two. The first 70% is used to design and sanity-check the rules; the final 30% is held back and measured **once**.
- Charges realistic trading costs on every position change — spread, commission, and slippage, all adjustable.
- Re-tests everything across 13 rolling walk-forward windows, so the conclusion does not rest on one lucky period.
- Produces a chart and a written report in an identical format every week.

## The main output: the decay curve

For each indicator, three numbers side by side:

| | What it shows |
|---|---|
| **In-sample, gross** | The flattering number that most backtests publish |
| **Out-of-sample, gross** | The same rule applied to data it has never seen |
| **Out-of-sample, net** | What you would actually have kept after costs |

A genuinely useful indicator shows three similar bars. The usual result is a tall first bar and a third bar at or below zero.

## The first result

Across **763 S&P 500 and FTSE 350 companies**, 2016–2026, with the final three
years held out and costs set at 8 basis points per side:

| Strategy | In-sample Sharpe | Out-of-sample Sharpe (gross) | Out-of-sample Sharpe (net) | Trades |
|---|---:|---:|---:|---:|
| EMA (20/50) | −0.15 | −0.79 | −0.85 | 60,426 |
| MACD (12/26/9) | 0.15 | −0.35 | −0.62 | 249,638 |
| RSI (14, 30/70) | 0.12 | 0.56 | **0.48** | 86,717 |
| VWAP (20-day) | −0.19 | −0.30 | −0.70 | 378,989 |
| **Buy & hold** | — | — | **1.50** | 0 |

Three of the four indicators produced a negative Sharpe ratio out-of-sample
after costs — they lost money net of what it cost to trade them. RSI, the only
mean-reversion rule of the four, was the only survivor, and it still lost
decisively to doing nothing at all.

## The live paper portfolio

Testing on history only proves so much, so the one surviving strategy is also
traded forward in a public paper portfolio:

```bash
python live/run_live.py                             # advance one session
python live/run_live.py --backfill-from 2026-02-20  # open the book
python live/run_live.py --status                    # show the book
```

Every order is committed on one day's close and filled at the next session's
open, paying the same costs as the research. The ledger is append-only: fills
are never edited, positions are replayed from the fill log rather than stored,
and the whole book is published as raw JSON alongside the page.

## How to run it

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python run_pipeline.py                    # smoke test on AAPL
python run_pipeline.py --universe all     # S&P + FTSE sample
python -m tests.test_indicators           # sanity checks
```

The report, data and charts are written to `reports/output/`.

## The website

The published site is generated from the same run. One command does both:

```bash
./publish.sh 2            # run week 2, rebuild the site
./publish.sh 2 --serve    # ...and preview at localhost:8000
```

The generator (`site/build.py`) reads the JSON each run emits and writes plain
static files to `site/dist/` — no framework, no JavaScript, and no dependencies
outside the standard library. Charts are rendered by the pipeline in both light
and dark variants and selected with `<picture>`.

See [DEPLOY.md](DEPLOY.md) for putting it on a custom domain.

## Repository layout

```
config.py          Every assumption in the project, in one file
data/loader.py     Downloading, cleaning, and caching price data
indicators/        EMA, VWAP, MACD, RSI — one file each, independently testable
backtest/
  engine.py        Turns signals into P&L. ~100 lines of pandas, no framework
  metrics.py       Sharpe, drawdown, win rate, turnover
  splits.py        Train/test splitting and walk-forward windows
reports/
  charts.py        Light and dark chart rendering
  weekly.py        The weekly markdown report
  data.py          Structured JSON export -- what the website builds from
site/
  build.py         Static site generator (standard library only)
  siteconfig.py    Domain and site metadata -- the one file to edit
  templates/       Page shells
  content/         Hand-written pages: methodology, about
  dist/            Generated output; this is what you deploy
tests/             17 sanity checks, including a lookahead-bias trap
run_pipeline.py    Run everything, write the report
publish.sh         Test, run, and rebuild the site in one step
```

## The design decisions worth knowing about

**No backtesting framework.** The engine is written in plain pandas rather than using a library like vectorbt. A framework would be faster, but this way every step from signal to P&L is visible and defensible line by line. The cost of a black box is that you cannot answer questions about it.

**One shared signal convention.** Every indicator returns the same thing: a target position of +1, 0, or −1. The one-bar shift that prevents lookahead bias happens in exactly one place, in the engine, so it cannot be applied to one strategy and forgotten in another.

**A test that tries to cheat.** `tests/test_indicators.py` builds a signal from each day's own return — information you only have once the day is over — and confirms the engine refuses to let it profit. It also checks that the *unshifted* version of the same signal produces an absurd Sharpe above 20, so that if anyone ever removes the safeguard, the test fails loudly instead of quietly printing better numbers.

**Verdicts assigned by rule, not by eye.** SURVIVED, WEAKENED, and FAILED are defined in code with fixed thresholds. This stops the standard from drifting week to week to suit the result — which is the exact failure this project is about.

## Known limitations

Stated in every report, because they do not go away:

- **Survivorship bias.** The universe uses current index membership, so companies that were delisted or dropped are missing. This flatters absolute returns. It affects the in-sample versus out-of-sample *comparison* far less, since both halves share the bias — which is why that comparison, not the absolute return, is the headline.
- **One cost assumption applied uniformly.** Real spreads vary by stock and by day, and widen exactly when you most want to trade. The cost-sensitivity chart shows how the conclusions change across the full range of assumptions rather than defending a single number.
- **What is labelled VWAP is a rolling volume-weighted moving average.** True VWAP is an intraday measure that resets each session and cannot be computed from daily bars at all. The retail community's daily-chart "VWAP" is the rolling version tested here. Calling it VWAP without this note would be imprecise, so the note appears every week.
- **No position sizing, leverage, or borrow costs.** Every position is the same size, and shorting is assumed free. It is not.

## Licence

MIT. The data comes from Yahoo Finance and is subject to their terms.
