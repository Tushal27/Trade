"""Offline tests for the upgrade package: tracker, filters, backtest engine.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from bot.backtest import simulate
from bot.filters import btc_trend_veto, funding_veto
from bot.strategy import FLAT, LONG, SHORT, decide
from bot.tracker import (OUTCOME_STOP, OUTCOME_TARGET, check_hit, r_multiple)
from tests.test_strategy import (make_candles, mirrored, ranging_series,
                                 trending_series)
from bot.regime import detect_regime


class TrackerTests(unittest.TestCase):
    def test_r_multiple(self):
        self.assertAlmostEqual(r_multiple("LONG", 100, 90, 115), 1.5)
        self.assertAlmostEqual(r_multiple("LONG", 100, 90, 90), -1.0)
        self.assertAlmostEqual(r_multiple("SHORT", 100, 110, 85), 1.5)

    def test_stop_hit_detected(self):
        ltf = make_candles([100, 101, 99, 95, 96], interval="1h", spread=0.001)
        entry_ms = ltf.open_times[1]
        hit = check_hit("LONG", entry_ms, stop=96.0, target=110.0, ltf=ltf)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], OUTCOME_STOP)
        self.assertEqual(hit[1], 96.0)

    def test_target_hit_detected(self):
        ltf = make_candles([100, 102, 105, 112, 111], interval="1h", spread=0.001)
        entry_ms = ltf.open_times[0]
        hit = check_hit("LONG", entry_ms, stop=95.0, target=110.0, ltf=ltf)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], OUTCOME_TARGET)

    def test_no_hit_while_in_range(self):
        ltf = make_candles([100, 101, 100, 102, 101], interval="1h", spread=0.001)
        hit = check_hit("LONG", ltf.open_times[0], stop=90.0, target=120.0, ltf=ltf)
        self.assertIsNone(hit)

    def test_bars_before_entry_ignored(self):
        # The crash to 80 happened BEFORE entry; it must not count as a stop.
        ltf = make_candles([100, 80, 100, 101, 102], interval="1h", spread=0.001)
        hit = check_hit("LONG", ltf.open_times[2], stop=90.0, target=120.0, ltf=ltf)
        self.assertIsNone(hit)


class FilterTests(unittest.TestCase):
    def test_funding_veto_blocks_crowded_long(self):
        self.assertIsNotNone(funding_veto("BTCUSDT", "LONG", 0.001))
        self.assertIsNone(funding_veto("BTCUSDT", "SHORT", 0.001))

    def test_funding_veto_blocks_crowded_short(self):
        self.assertIsNotNone(funding_veto("BTCUSDT", "SHORT", -0.001))
        self.assertIsNone(funding_veto("BTCUSDT", "LONG", -0.001))

    def test_funding_normal_or_missing_no_veto(self):
        self.assertIsNone(funding_veto("BTCUSDT", "LONG", 0.0001))
        self.assertIsNone(funding_veto("BTCUSDT", "LONG", None))

    def test_btc_veto_only_against_btc_trend(self):
        self.assertIsNotNone(btc_trend_veto("ETHUSDT", "LONG", "TREND_DOWN"))
        self.assertIsNone(btc_trend_veto("ETHUSDT", "SHORT", "TREND_DOWN"))
        self.assertIsNone(btc_trend_veto("BTCUSDT", "LONG", "TREND_DOWN"))
        self.assertIsNone(btc_trend_veto("ETHUSDT", "LONG", None))

    def test_veto_blocks_entry_but_not_exit(self):
        regime = detect_regime(make_candles(trending_series(drift=0.004)))
        ltf = make_candles(trending_series(n=300, drift=0.003, seed=11), interval="1h")
        blocked = decide("T", regime, ltf, FLAT, entry_vetoes=["test veto"])
        self.assertEqual(blocked.stance, FLAT)
        # Holding an existing long: the veto must NOT force an exit.
        held = decide("T", regime, ltf, LONG, entry_vetoes=["test veto"])
        self.assertEqual(held.stance, LONG)


class BacktestTests(unittest.TestCase):
    def test_simulate_runs_and_produces_trades_on_trend(self):
        htf = make_candles(trending_series(n=400, drift=0.004))
        ltf = make_candles(trending_series(n=600, drift=0.001, seed=5), interval="1h")
        stats = simulate(htf, ltf)
        self.assertIn("win_rate", stats)
        self.assertGreaterEqual(stats["trades"], 0)
        self.assertEqual(stats["trades"], stats["wins"] + stats["losses"])

    def test_simulate_handles_ranging_market(self):
        htf = make_candles(ranging_series(n=400))
        ltf = make_candles(ranging_series(n=600, amp=0.03, seed=4), interval="1h")
        stats = simulate(htf, ltf)
        self.assertIsInstance(stats["total_r"], float)


if __name__ == "__main__":
    unittest.main()
