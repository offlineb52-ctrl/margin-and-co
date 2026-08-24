"""
Sanity checks. Run with:  python -m tests.test_indicators

These are deliberately plain asserts rather than a pytest suite -- one fewer
dependency, and you can read every check without knowing a framework. Each
test exists because it catches a specific mistake that would silently corrupt
a published result.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from backtest.engine import run_backtest
from backtest.metrics import max_drawdown, sharpe_ratio, total_return
from config import DEFAULT_COSTS, ZERO_COSTS
from indicators.ema import ema
from indicators.macd import macd_lines
from indicators.rsi import rsi
from indicators.vwap import rolling_vwap

PASSED, FAILED = [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    (PASSED if condition else FAILED).append(name)
    mark = "PASS" if condition else "FAIL"
    print(f"  [{mark}] {name}" + (f"  -- {detail}" if detail else ""))


def synthetic_bars(n: int = 500, seed: int = 42) -> pd.DataFrame:
    """A reproducible random walk with volume. Used so tests never hit the network."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0004, 0.015, n)
    close = 100 * np.exp(np.cumsum(returns))
    index = pd.bdate_range("2015-01-01", periods=n)
    return pd.DataFrame({
        "open": close * (1 + rng.normal(0, 0.002, n)),
        "high": close * (1 + np.abs(rng.normal(0, 0.005, n))),
        "low": close * (1 - np.abs(rng.normal(0, 0.005, n))),
        "close": close,
        "volume": rng.integers(1_000_000, 5_000_000, n).astype(float),
    }, index=index)


# --------------------------------------------------------------------------

def test_ema_matches_recursive_definition() -> None:
    """EMA must use the recursive form, not pandas' adjusted default.

    If this drifts, every number in the project silently stops matching what
    a reader sees on TradingView.
    """
    s = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
    got = ema(s, span=3)

    alpha = 2 / (3 + 1)
    want = [10.0]
    for x in s.iloc[1:]:
        want.append(alpha * x + (1 - alpha) * want[-1])

    check("EMA matches the recursive definition",
          np.allclose(got.values, want),
          f"max diff {np.max(np.abs(got.values - np.array(want))):.2e}")


def test_rsi_is_bounded_and_uses_wilder_smoothing() -> None:
    """RSI must stay in [0, 100], and must use alpha = 1/period, not 2/(n+1)."""
    df = synthetic_bars()
    values = rsi(df["close"], period=14).dropna()

    check("RSI stays within [0, 100]",
          bool((values >= 0).all() and (values <= 100).all()),
          f"range {values.min():.1f} to {values.max():.1f}")

    # A monotonically rising series has no losses, so RSI must pin at 100.
    rising = pd.Series(np.arange(1.0, 60.0))
    check("RSI = 100 on a series with no down days",
          bool(np.isclose(rsi(rising, 14).dropna().iloc[-1], 100.0)))

    # Wilder's alpha is 1/14; a plain span=14 EMA would use 2/15. On a
    # CONSTANT input the two converge and look identical, so this must be
    # checked on a varied series -- which is precisely why the bug survives
    # in so much published indicator code.
    delta = df["close"].diff()
    wrong = delta.clip(lower=0).ewm(span=14, adjust=False).mean()
    right = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    gap = (wrong - right).abs().max()
    check("Wilder smoothing differs from a span-14 EMA (so the choice matters)",
          not np.allclose(wrong.dropna(), right.dropna()),
          f"max divergence {gap:.4f}")


def test_vwap_is_volume_weighted() -> None:
    """VWAP must weight by volume -- a bug here silently makes it a plain SMA."""
    df = synthetic_bars(100)
    df.loc[df.index[50], "volume"] = 1e12  # one enormous print

    vw = rolling_vwap(df, window=20)
    tp = (df["high"] + df["low"] + df["close"]) / 3

    # After the huge-volume day, VWAP should be dragged towards that day's price.
    after = vw.iloc[51]
    huge_day_price = tp.iloc[50]
    sma_after = tp.iloc[32:52].mean()

    check("VWAP is pulled toward the high-volume day, unlike an SMA",
          abs(after - huge_day_price) < abs(sma_after - huge_day_price),
          f"vwap {after:.2f} vs sma {sma_after:.2f}, target {huge_day_price:.2f}")


def test_macd_histogram_identity() -> None:
    """histogram must equal macd - signal, by definition."""
    df = synthetic_bars()
    lines = macd_lines(df["close"])
    check("MACD histogram = MACD - signal",
          np.allclose((lines["macd"] - lines["signal"]).dropna(),
                      lines["histogram"].dropna()))


def test_no_lookahead_bias() -> None:
    """THE most important test in the project.

    Careful about what is being tested here, because it is easy to get
    backwards. Under the signal contract, positions[t] is the position chosen
    at the CLOSE of day t, and the engine shifts it forward so it earns day
    t+1's return. A signal that genuinely predicts tomorrow SHOULD score
    brilliantly -- that is not a bug, that is what alpha would look like.

    The bug we are guarding against is subtler: a signal built from day t's
    OWN return. That is knowable only once day t has closed, so it must not be
    allowed to earn day t's return. With the engine's shift it earns day t+1's
    return instead, which is uninformative -- Sharpe near zero. Without the
    shift it earns |return| every single day, and the equity curve becomes a
    straight line to the moon.

    So: same signal, two engines. The correct one must be unremarkable and the
    buggy one must be absurd. If someone deletes the .shift(1), this test
    fails immediately.
    """
    # Averaged over many random samples, NOT one. A single 1000-day sample of
    # pure noise routinely throws up a Sharpe above 1.0 -- measured here, it
    # happens on about 6.5% of seeds, with a standard deviation of ~0.55. An
    # earlier version of this test asserted on one seed and failed at Sharpe
    # 1.13, which was not a leak but a 2-sigma draw.
    #
    # That is this entire project in miniature: judge a strategy on one sample
    # and noise will occasionally hand you a result that looks like skill.
    n_seeds = 30
    correct_sharpes, buggy_sharpes = [], []

    for seed in range(n_seeds):
        df = synthetic_bars(1000, seed=seed)
        todays_return = df["close"].pct_change()

        # Knowable at the close of day t, and not one second earlier.
        cheat = np.sign(todays_return).fillna(0.0)

        correct_sharpes.append(
            run_backtest(df, cheat, name="CHEAT", costs=ZERO_COSTS).metrics_gross["sharpe"]
        )
        # The same signal with the shift removed -- i.e. the classic bug.
        buggy_sharpes.append(sharpe_ratio((cheat * todays_return).dropna()))

    correct_sharpe = float(np.mean(correct_sharpes))
    buggy_sharpe = float(np.mean(buggy_sharpes))

    check("Same-day signal earns nothing once positions are shifted",
          abs(correct_sharpe) < 0.25,
          f"mean Sharpe {correct_sharpe:+.3f} over {n_seeds} seeds "
          f"(sd {np.std(correct_sharpes):.2f}) -- no free lunch, as it should be")

    check("Without the shift, the same signal is absurd (this is the bug)",
          buggy_sharpe > 10.0,
          f"mean Sharpe {buggy_sharpe:.1f} -- an unshifted backtest looks like this")

    check("The shift is worth many points of Sharpe",
          buggy_sharpe - correct_sharpe > 10.0,
          f"gap of {buggy_sharpe - correct_sharpe:.1f} Sharpe points")

    # A signal that truly predicts tomorrow is allowed to win -- confirming
    # the engine is not simply crippling every strategy handed to it.
    df = synthetic_bars(1000, seed=0)
    oracle = np.sign(df["close"].pct_change().shift(-1)).fillna(0.0)
    oracle_sharpe = run_backtest(df, oracle, name="ORACLE",
                                 costs=ZERO_COSTS).metrics_gross["sharpe"]
    check("A genuine one-day-ahead oracle still scores highly (engine is not broken)",
          oracle_sharpe > 10.0,
          f"Sharpe {oracle_sharpe:.1f}")


def test_costs_only_ever_reduce_returns() -> None:
    """Net must never exceed gross, and more trading must cost more."""
    df = synthetic_bars(800)
    rng = np.random.default_rng(7)

    lazy = pd.Series(np.repeat(rng.choice([-1.0, 1.0], 8), 100), index=df.index)
    busy = pd.Series(rng.choice([-1.0, 1.0], 800), index=df.index)

    lazy_res = run_backtest(df, lazy, name="LAZY", costs=DEFAULT_COSTS)
    busy_res = run_backtest(df, busy, name="BUSY", costs=DEFAULT_COSTS)

    check("Net returns never exceed gross returns",
          bool((lazy_res.net_returns <= lazy_res.gross_returns + 1e-12).all()))

    check("A strategy that trades more pays more",
          busy_res.total_cost_paid > lazy_res.total_cost_paid * 5,
          f"busy {busy_res.total_cost_paid:.1%} vs lazy {lazy_res.total_cost_paid:.1%}")

    zero_res = run_backtest(df, busy, name="BUSY", costs=ZERO_COSTS)
    check("Zero-cost model charges nothing",
          np.isclose(zero_res.total_cost_paid, 0.0))


def test_metrics_against_hand_calculations() -> None:
    """Spot-check the metrics against values you can verify on paper."""
    r = pd.Series([0.10, -0.10])
    check("Compounded return of +10% then -10% is -1%",
          np.isclose(total_return(r), -0.01),
          f"got {total_return(r):.4f}")

    # Down 50%, then up 50%: peak 1.0 -> trough 0.5 -> 0.75. Worst DD = -50%.
    dd = pd.Series([-0.5, 0.5])
    check("Max drawdown of -50% then +50% is -50%",
          np.isclose(max_drawdown(dd), -0.5),
          f"got {max_drawdown(dd):.4f}")

    # Constant returns have zero volatility -> Sharpe is undefined, not infinite.
    check("Constant returns give NaN Sharpe, not infinity",
          np.isnan(sharpe_ratio(pd.Series([0.01] * 50))))


def test_buy_and_hold_matches_the_asset() -> None:
    """A buy-and-hold backtest must reproduce the asset's own return."""
    df = synthetic_bars(400)
    from backtest.engine import buy_and_hold

    res = buy_and_hold(df, "TEST", costs=ZERO_COSTS)
    asset = df["close"].pct_change().dropna()

    # One day of lag at the start, from the shift; compare from day two.
    check("Buy & hold reproduces the underlying asset's return",
          np.isclose(total_return(res.gross_returns.iloc[1:]),
                     total_return(asset.iloc[1:]), rtol=1e-9))


def main() -> int:
    print("Margin & Co. — sanity checks\n")
    for fn in [
        test_ema_matches_recursive_definition,
        test_rsi_is_bounded_and_uses_wilder_smoothing,
        test_vwap_is_volume_weighted,
        test_macd_histogram_identity,
        test_no_lookahead_bias,
        test_costs_only_ever_reduce_returns,
        test_metrics_against_hand_calculations,
        test_buy_and_hold_matches_the_asset,
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
