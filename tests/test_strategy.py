"""Offline sanity tests: synthetic regimes must produce the expected decisions.

Run with:  python -m unittest discover -s tests -v
No network access required.
"""

from __future__ import annotations

import math
import random
import time
import unittest

from bot.data import Candles
from bot.notifier import telegram_configured
from bot.regime import RANGE, TREND_DOWN, TREND_UP, detect_regime
from bot.state import get_stance, set_stance
from bot.strategy import FLAT, LONG, SHORT, decide


def make_candles(closes: list[float], interval: str = "4h", spread: float = 0.004) -> Candles:
    """Wrap a close series into a Candles object with plausible OHLC."""
    interval_ms = {"1h": 3_600_000, "4h": 14_400_000}[interval]
    n = len(closes)
    end = int(time.time() * 1000) - interval_ms  # last bar already closed
    times = [end - (n - 1 - i) * interval_ms for i in range(n)]
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) * (1 + spread) for o, c in zip(opens, closes)]
    lows = [min(o, c) * (1 - spread) for o, c in zip(opens, closes)]
    vols = [1000.0] * n
    return Candles("TESTUSDT", interval, times, opens, highs, lows, closes, vols)


def trending_series(n: int = 400, start: float = 100.0, drift: float = 0.004, seed: int = 7) -> list[float]:
    """Trend with shallow periodic pullbacks (so momentum oscillators behave
    like they do in real trends rather than pinning at the extremes)."""
    rng = random.Random(seed)
    closes, price = [], start
    for i in range(n):
        pullback = -1.4 * drift * (1 + math.sin(i / 6.0)) / 2  # periodic counter-drift
        price *= math.exp(drift + pullback + rng.gauss(0, 0.004))
        closes.append(price)
    return closes


def mirrored(series: list[float], start: float = 100.0) -> list[float]:
    """Geometric mirror: identical pullback structure with negated log-returns."""
    return [start * start / p for p in series]


def ranging_series(n: int = 400, center: float = 100.0, amp: float = 0.05, seed: int = 7) -> list[float]:
    rng = random.Random(seed)
    return [center * (1 + amp * math.sin(i / 9.0) + rng.gauss(0, 0.002)) for i in range(n)]


class RegimeTests(unittest.TestCase):
    def test_uptrend_detected(self):
        regime = detect_regime(make_candles(trending_series(drift=0.004)))
        self.assertEqual(regime.trend, TREND_UP)

    def test_downtrend_detected(self):
        regime = detect_regime(make_candles(mirrored(trending_series(drift=0.004))))
        self.assertEqual(regime.trend, TREND_DOWN)

    def test_range_detected(self):
        regime = detect_regime(make_candles(ranging_series()))
        self.assertEqual(regime.trend, RANGE)


class StrategyTests(unittest.TestCase):
    def test_uptrend_produces_long_with_plan(self):
        closes = trending_series(drift=0.004)
        regime = detect_regime(make_candles(closes))
        ltf = make_candles(trending_series(n=300, drift=0.003, seed=11), interval="1h")
        d = decide("TESTUSDT", regime, ltf, prev_stance=FLAT)
        self.assertEqual(d.stance, LONG)
        self.assertIsNotNone(d.stop)
        self.assertIsNotNone(d.target)
        self.assertLess(d.stop, d.price)
        self.assertGreater(d.target, d.price)

    def test_downtrend_produces_short(self):
        regime = detect_regime(make_candles(mirrored(trending_series(drift=0.004))))
        ltf = make_candles(mirrored(trending_series(n=300, drift=0.003, seed=11)), interval="1h")
        d = decide("TESTUSDT", regime, ltf, prev_stance=FLAT)
        self.assertEqual(d.stance, SHORT)
        self.assertGreater(d.stop, d.price)
        self.assertLess(d.target, d.price)

    def test_long_held_while_trend_intact(self):
        regime = detect_regime(make_candles(trending_series(drift=0.004)))
        ltf = make_candles(trending_series(n=300, drift=0.003, seed=11), interval="1h")
        d = decide("TESTUSDT", regime, ltf, prev_stance=LONG)
        self.assertEqual(d.stance, LONG)

    def test_long_exited_when_trend_breaks(self):
        # 4h flips to a downtrend while we hold a long -> must not stay long.
        regime = detect_regime(make_candles(mirrored(trending_series(drift=0.004))))
        ltf = make_candles(mirrored(trending_series(n=300, drift=0.003, seed=11)), interval="1h")
        d = decide("TESTUSDT", regime, ltf, prev_stance=LONG)
        self.assertNotEqual(d.stance, LONG)

    def test_range_midprice_stays_flat(self):
        closes = ranging_series()
        regime = detect_regime(make_candles(closes))
        # Mid-range 1h series: gentle noise around the center, RSI ~50.
        ltf = make_candles(ranging_series(n=300, amp=0.01, seed=3), interval="1h")
        d = decide("TESTUSDT", regime, ltf, prev_stance=FLAT)
        self.assertEqual(d.stance, FLAT)


class NotifierTests(unittest.TestCase):
    def test_telegram_skipped_when_unconfigured(self):
        import os
        saved = {k: os.environ.pop(k, None) for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")}
        try:
            self.assertFalse(telegram_configured())
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class StateTests(unittest.TestCase):
    def test_stance_roundtrip_and_since_preserved(self):
        state: dict = {}
        set_stance(state, "BTCUSDT", LONG, 50_000.0)
        first_since = state["BTCUSDT"]["since"]
        set_stance(state, "BTCUSDT", LONG, 51_000.0)
        self.assertEqual(state["BTCUSDT"]["since"], first_since)
        self.assertEqual(get_stance(state, "BTCUSDT"), LONG)
        set_stance(state, "BTCUSDT", FLAT, 49_000.0)
        self.assertEqual(get_stance(state, "BTCUSDT"), FLAT)
        # On a stance change, `since` is reset to the new update time.
        self.assertEqual(state["BTCUSDT"]["since"], state["BTCUSDT"]["updated_at"])


if __name__ == "__main__":
    unittest.main()
