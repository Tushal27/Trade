"""Decision layer: turn regime + 1h structure into a stance and trade plan.

Stances are LONG / SHORT / FLAT. Entries are stricter than exits (hysteresis)
so the bot does not flip-flop on every bar:

  TREND regimes  -> trend-following: 1h EMA20/EMA50 aligned with the 4h trend,
                    RSI filter to avoid chasing exhaustion.
                    Exit only when price closes through the 1h EMA50.
  RANGE regime   -> mean reversion: RSI extreme + Bollinger band touch,
                    exit at the middle band or when RSI normalizes.

Every actionable signal carries an ATR-based suggested stop and target
(1.5 ATR risk, 1.5R reward) so the email is a complete trade plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .data import Candles
from .indicators import atr, bollinger, ema_series, rsi
from .regime import EXPANSION, RANGE, TREND_DOWN, TREND_UP, Regime

LONG = "LONG"
SHORT = "SHORT"
FLAT = "FLAT"

STOP_ATR = 1.5
REWARD_R = 1.5  # target = entry +/- STOP_ATR * REWARD_R * ATR


@dataclass
class Decision:
    symbol: str
    stance: str                    # desired stance after this bar
    reasons: list[str] = field(default_factory=list)
    price: float = 0.0
    stop: float | None = None
    target: float | None = None
    regime_label: str = ""
    confidence: float = 0.0


def decide(symbol: str, regime: Regime, ltf: Candles, prev_stance: str) -> Decision:
    closes = ltf.closes
    price = closes[-1]
    ema20 = ema_series(closes, 20)[-1]
    ema50 = ema_series(closes, 50)[-1]
    cur_rsi = rsi(closes, 14)
    cur_atr = atr(ltf.highs, ltf.lows, closes, 14)
    lower, mid, upper = bollinger(closes, 20, 2.0)

    d = Decision(symbol=symbol, stance=FLAT, price=price,
                 regime_label=regime.label, confidence=regime.confidence)

    # --- holding a position: check exit conditions first (looser than entries) ---
    if prev_stance == LONG:
        if regime.trend == TREND_UP and price >= ema50:
            d.stance = LONG
            d.reasons.append("Uptrend intact: price holding above 1h EMA50.")
            return d
        if regime.trend == RANGE and prev_held_mean_reversion_valid(price, mid, cur_rsi, LONG):
            d.stance = LONG
            d.reasons.append("Mean-reversion long still working toward the middle band.")
            return d
        d.reasons.append("Long invalidated: trend/mean-reversion conditions no longer hold.")
        # fall through to FLAT (or a fresh short below)

    if prev_stance == SHORT:
        if regime.trend == TREND_DOWN and price <= ema50:
            d.stance = SHORT
            d.reasons.append("Downtrend intact: price holding below 1h EMA50.")
            return d
        if regime.trend == RANGE and prev_held_mean_reversion_valid(price, mid, cur_rsi, SHORT):
            d.stance = SHORT
            d.reasons.append("Mean-reversion short still working toward the middle band.")
            return d
        d.reasons.append("Short invalidated: trend/mean-reversion conditions no longer hold.")

    # --- flat (or just invalidated): look for a fresh entry ---
    if regime.trend == TREND_UP:
        if ema20 > ema50 and price > ema20 and cur_rsi < 70:
            d.stance = LONG
            d.reasons.append(
                f"Trend entry: 4h uptrend + 1h EMA20>EMA50, price above EMA20, RSI {cur_rsi:.0f} not overbought."
            )
            _attach_plan(d, price, cur_atr, LONG)
        else:
            d.reasons.append("4h uptrend but 1h entry conditions not aligned — waiting.")
    elif regime.trend == TREND_DOWN:
        if ema20 < ema50 and price < ema20 and cur_rsi > 30:
            d.stance = SHORT
            d.reasons.append(
                f"Trend entry: 4h downtrend + 1h EMA20<EMA50, price below EMA20, RSI {cur_rsi:.0f} not oversold."
            )
            _attach_plan(d, price, cur_atr, SHORT)
        else:
            d.reasons.append("4h downtrend but 1h entry conditions not aligned — waiting.")
    else:  # RANGE
        if regime.volatility == EXPANSION:
            # Fading extremes during a volatility blow-up is how accounts die.
            d.reasons.append("Ranging but volatility is expanding — mean reversion disabled, standing aside.")
        elif cur_rsi < 30 and price <= lower:
            d.stance = LONG
            d.reasons.append(f"Range entry: RSI {cur_rsi:.0f} oversold at lower Bollinger band; targeting the mean.")
            _attach_plan(d, price, cur_atr, LONG, target_override=mid)
        elif cur_rsi > 70 and price >= upper:
            d.stance = SHORT
            d.reasons.append(f"Range entry: RSI {cur_rsi:.0f} overbought at upper Bollinger band; targeting the mean.")
            _attach_plan(d, price, cur_atr, SHORT, target_override=mid)
        else:
            d.reasons.append("Range regime, price mid-range — no edge, staying flat.")

    return d


def prev_held_mean_reversion_valid(price: float, mid: float, cur_rsi: float, side: str) -> bool:
    """A mean-reversion position stays on until price reaches the middle band
    or momentum has fully normalized."""
    if side == LONG:
        return price < mid and cur_rsi < 55
    return price > mid and cur_rsi > 45


def _attach_plan(d: Decision, price: float, cur_atr: float, side: str, target_override: float | None = None) -> None:
    risk = STOP_ATR * cur_atr
    if side == LONG:
        d.stop = price - risk
        d.target = target_override if target_override is not None else price + risk * REWARD_R
    else:
        d.stop = price + risk
        d.target = target_override if target_override is not None else price - risk * REWARD_R
