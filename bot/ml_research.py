"""Honest ML research harness: can a Random Forest predict next-day BTC?

This exists to get the TRUTH, not a flattering number. It is built to avoid
the four bugs that manufacture fake 70-80% accuracy:

  1. Chronological walk-forward — the model only ever trains on the PAST and is
     tested on a LATER period it has never seen. No shuffled split (the #1 leak).
  2. No look-ahead features — every feature is computed from data up to and
     including the prior close; the target is the NEXT bar's direction.
  3. Profit, not accuracy — it simulates trading the predictions with fees and
     reports return, because a model can be "accurate" on tiny moves and still
     lose money. Accuracy != money.
  4. Honest baselines — it prints the majority-class accuracy and buy & hold so
     you can see whether the model beats doing nothing clever.

Run via the "ML Research" workflow (needs numpy + scikit-learn).
  python -m bot.ml_research --days 1500 --email
"""

from __future__ import annotations

import argparse
import sys
import time

from .backtest import fetch_klines_range
from .data import Candles
from .indicators import atr, ema_series, realized_vol, rsi

try:
    import numpy as np
    from sklearn.ensemble import RandomForestClassifier
except ImportError:  # libraries are installed only in the ML workflow
    np = None
    RandomForestClassifier = None

FEE = 0.0016        # round-trip fee+slippage, same assumption as the price backtest
WARMUP = 60         # bars needed before the first feature row
TRAIN_MIN = 400     # minimum training bars before the first walk-forward test
TEST_SIZE = 60      # out-of-sample block size per walk-forward step


def build_dataset(c: Candles):
    """Feature matrix X, target y (1 = next bar up), and the bar index per row.

    Row i uses only closes/highs/lows/volumes up to bar i; y is the sign of the
    move from bar i to bar i+1 — strictly no look-ahead.
    """
    closes, highs, lows, vols = c.closes, c.highs, c.lows, c.volumes
    rows, targets, idxs = [], [], []
    for i in range(WARMUP, len(closes) - 1):
        w = closes[: i + 1]
        avg_vol5 = sum(vols[i - 5:i]) / 5 if sum(vols[i - 5:i]) > 0 else 0
        rows.append([
            closes[i] / closes[i - 1] - 1,
            closes[i] / closes[i - 2] - 1,
            closes[i] / closes[i - 3] - 1,
            closes[i] / closes[i - 5] - 1,
            closes[i] / closes[i - 10] - 1,
            rsi(w, 14),
            closes[i] / ema_series(w, 20)[-1] - 1,
            closes[i] / ema_series(w, 50)[-1] - 1,
            atr(highs[: i + 1], lows[: i + 1], closes[: i + 1], 14) / closes[i],
            realized_vol(w, 10),
            (vols[i] / avg_vol5 - 1) if avg_vol5 > 0 else 0.0,
        ])
        targets.append(1 if closes[i + 1] > closes[i] else 0)
        idxs.append(i)
    return np.array(rows), np.array(targets), idxs


def walk_forward(X, y):
    """Expanding-window walk-forward. Returns list of (dataset_row, prediction).

    Trains on rows [0:pos], predicts the next TEST_SIZE block, rolls forward.
    The model never sees a bar at or after the one it predicts.
    """
    preds = []
    pos = TRAIN_MIN
    while pos < len(X):
        model = RandomForestClassifier(
            n_estimators=200, max_depth=5, min_samples_leaf=20,
            random_state=42, n_jobs=-1,
        )
        model.fit(X[:pos], y[:pos])
        block = model.predict(X[pos:pos + TEST_SIZE])
        for j, p in enumerate(block):
            preds.append((pos + j, int(p)))
        pos += TEST_SIZE
    return preds


def evaluate(preds, y, closes, idxs) -> dict:
    correct = 0
    rets = []
    actuals = []
    for ds_i, pred in preds:
        actual = int(y[ds_i])
        actuals.append(actual)
        if pred == actual:
            correct += 1
        i = idxs[ds_i]
        day_ret = closes[i + 1] / closes[i] - 1
        rets.append((day_ret if pred == 1 else -day_ret) - FEE)
    rets = np.array(rets)
    n = len(preds)
    acc = correct / n if n else 0.0
    up_rate = sum(actuals) / n if n else 0.0
    majority = max(up_rate, 1 - up_rate)  # accuracy of always guessing the common class

    strat_total = (np.prod(1 + rets) - 1) * 100
    # buy & hold over the same tested span
    first_i, last_i = idxs[preds[0][0]], idxs[preds[-1][0]] + 1
    bh_total = (closes[last_i] / closes[first_i] - 1) * 100
    sharpe = (rets.mean() / rets.std() * (252 ** 0.5)) if rets.std() > 0 else 0.0
    return {
        "n": n, "accuracy": acc, "majority_baseline": majority,
        "edge_over_baseline": acc - majority,
        "strategy_return_pct": strat_total, "buyhold_return_pct": bh_total,
        "sharpe": sharpe,
    }


def format_report(symbol: str, days: int, r: dict) -> str:
    verdict = (
        "LIKELY REAL — investigate further with more out-of-sample data"
        if r["edge_over_baseline"] > 0.04 and r["strategy_return_pct"] > r["buyhold_return_pct"]
        else "NO EDGE — accuracy ~ guessing the common class; do NOT trade this"
    )
    return "\n".join([
        f"ML walk-forward research — {symbol}, last {days} days, fee {FEE*100:.2f}%/trade",
        "Chronological split, no look-ahead, profit-measured.",
        "",
        f"  Out-of-sample predictions: {r['n']}",
        f"  Directional accuracy:      {r['accuracy']:.1%}",
        f"  Majority-class baseline:   {r['majority_baseline']:.1%}   <- beat THIS, not 50%",
        f"  Edge over baseline:        {r['edge_over_baseline']:+.1%}",
        "",
        f"  Strategy return (net):     {r['strategy_return_pct']:+.1f}%",
        f"  Buy & hold (same span):    {r['buyhold_return_pct']:+.1f}%",
        f"  Strategy Sharpe (ann.):    {r['sharpe']:.2f}",
        "",
        f"  VERDICT: {verdict}",
        "",
        "Reminder: a real next-day edge in a liquid market is tiny (~1-3% over",
        "baseline). Anything near 70-80% means a leak, not a discovery.",
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Leakage-free ML research on next-bar direction")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--days", type=int, default=1500)
    parser.add_argument("--email", action="store_true")
    args = parser.parse_args()

    if np is None:
        print("[ERROR] numpy + scikit-learn not installed. Run via the ML Research workflow.",
              file=sys.stderr)
        return 1

    end_ms = int(time.time() * 1000)
    start_ms = end_ms - (args.days + WARMUP + 5) * 86_400_000
    print(f"Fetching {args.symbol} daily history…")
    candles = fetch_klines_range(args.symbol, "1d", start_ms, end_ms)
    print(f"Building features from {len(candles)} daily bars…")
    X, y, idxs = build_dataset(candles)
    print(f"Walk-forward training/testing ({len(X)} samples)…")
    preds = walk_forward(X, y)
    report = format_report(args.symbol, args.days, evaluate(preds, y, candles.closes, idxs))
    print("\n" + report)

    if args.email:
        from .main import dispatch
        dispatch(f"🤖 ML Research — {args.symbol} next-day", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
