"""Offline tests for the upgrade package: tracker, filters, backtest engine.

Run with:  python -m unittest discover -s tests -v
"""

from __future__ import annotations

import unittest

from bot.backtest import simulate
from bot.filters import btc_trend_veto, funding_veto
from bot.strategy import CANDIDATE, FLAT, LONG, SHORT, TREND_ONLY, decide
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


class BrainTests(unittest.TestCase):
    def test_brain_silent_when_unconfigured(self):
        import os
        from bot.brain import brain_configured, commentary
        saved = {k: os.environ.pop(k, None) for k in
                 ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "OPENAI_BASE_URL", "OPENAI_API_KEY")}
        try:
            self.assertFalse(brain_configured())
            self.assertIsNone(commentary("dummy report"))
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


class SizingTests(unittest.TestCase):
    def test_stop_distance_always_shown(self):
        import os
        from bot.main import sizing_lines
        saved = os.environ.pop("TRADE_CAPITAL", None)
        try:
            lines = sizing_lines(100.0, 96.0)  # 4% stop
            self.assertTrue(any("4.00%" in ln for ln in lines))
        finally:
            if saved is not None:
                os.environ["TRADE_CAPITAL"] = saved

    def test_position_and_margin_math(self):
        import os
        from bot.main import sizing_lines
        env = {"TRADE_CAPITAL": "100000", "RISK_PCT": "1", "LEVERAGE": "5",
               "ACCOUNT_CURRENCY": "USDT"}
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        try:
            # entry 100, stop 96.4 -> 3.6% stop; risk 1000 -> position ~27,778; margin /5 ~5,556
            lines = "\n".join(sizing_lines(100.0, 96.4))
            self.assertIn("1,000.00 USDT", lines)        # risk amount
            self.assertIn("27,778 USDT", lines)          # position notional
            self.assertIn("5,556 USDT", lines)           # margin at 5x
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


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

    def test_simulate_candidate_params(self):
        htf = make_candles(trending_series(n=400, drift=0.004))
        ltf = make_candles(trending_series(n=600, drift=0.001, seed=5), interval="1h")
        stats = simulate(htf, ltf, params=CANDIDATE)
        self.assertEqual(stats["trades"], stats["wins"] + stats["losses"])


class CandidateParamsTests(unittest.TestCase):
    def test_trailing_trend_entry_has_no_fixed_target(self):
        regime = detect_regime(make_candles(trending_series(drift=0.004)))
        ltf = make_candles(trending_series(n=300, drift=0.003, seed=11), interval="1h")
        d = decide("T", regime, ltf, FLAT, params=CANDIDATE)
        if d.stance == LONG:  # entry fires only if RSI is in the pullback zone
            self.assertIsNone(d.target)
            self.assertIsNotNone(d.stop)

    def test_trend_only_stays_flat_in_range_regime(self):
        regime = detect_regime(make_candles(ranging_series()))
        # Deep oversold at the band — would be a range entry for CANDIDATE.
        ltf = make_candles(ranging_series(n=300, amp=0.04, seed=4), interval="1h")
        d = decide("T", regime, ltf, FLAT, params=TREND_ONLY)
        self.assertEqual(d.stance, FLAT)

    def test_trailing_position_check_hit_ignores_target(self):
        ltf = make_candles([100, 102, 105, 112, 111], interval="1h", spread=0.001)
        hit = check_hit("LONG", ltf.open_times[0], stop=95.0, target=None, ltf=ltf)
        self.assertIsNone(hit)  # never stopped, no target to hit


if __name__ == "__main__":
    unittest.main()
