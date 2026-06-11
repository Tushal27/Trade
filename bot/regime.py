"""Regime detection: classify the higher-timeframe (4h) market state.

Two axes, deliberately coarse to resist overfitting:
  - trend:      TREND_UP / TREND_DOWN / RANGE   (EMA50 vs EMA200 alignment)
  - volatility: COMPRESSION / NORMAL / EXPANSION (realized-vol percentile)
"""

from __future__ import annotations

from dataclasses import dataclass

from .data import Candles
from .indicators import atr, ema_series, percentile_rank, realized_vol

TREND_UP = "TREND_UP"
TREND_DOWN = "TREND_DOWN"
RANGE = "RANGE"

COMPRESSION = "COMPRESSION"
NORMAL = "NORMAL"
EXPANSION = "EXPANSION"

VOL_WINDOW = 20          # bars per realized-vol estimate
VOL_HISTORY = 150        # how many trailing vol readings form the percentile base
EXPANSION_PCTL = 0.70
COMPRESSION_PCTL = 0.30


@dataclass
class Regime:
    trend: str
    volatility: str
    confidence: float  # 0..1, how decisive the trend classification is
    vol_percentile: float

    @property
    def label(self) -> str:
        return f"{self.trend} / {self.volatility}"


def detect_regime(htf: Candles) -> Regime:
    """Classify regime from higher-timeframe candles (expects >= 250 bars)."""
    closes = htf.closes
    ema50 = ema_series(closes, 50)[-1]
    ema200 = ema_series(closes, 200)[-1]
    price = closes[-1]

    # Trend axis: require full alignment of price and both EMAs; anything
    # mixed is treated as RANGE (the safer default).
    if price > ema50 > ema200:
        trend = TREND_UP
    elif price < ema50 < ema200:
        trend = TREND_DOWN
    else:
        trend = RANGE

    # Confidence: EMA separation normalized by ATR. A wide, clean separation
    # relative to recent bar size means the trend reading is more decisive.
    bar_atr = atr(htf.highs, htf.lows, closes, 14)
    separation = abs(ema50 - ema200) / bar_atr if bar_atr > 0 else 0.0
    confidence = min(separation / 3.0, 1.0)  # ~3 ATRs of separation = full confidence
    if trend == RANGE:
        confidence = 1.0 - confidence  # mixed EMAs close together = confidently ranging

    # Volatility axis: where does current realized vol sit vs its own history?
    vol_now = realized_vol(closes, VOL_WINDOW)
    history = []
    start = max(VOL_WINDOW + 1, len(closes) - VOL_HISTORY)
    for i in range(start, len(closes)):
        history.append(realized_vol(closes[: i + 1], VOL_WINDOW))
    pctl = percentile_rank(history, vol_now)
    if pctl >= EXPANSION_PCTL:
        volatility = EXPANSION
    elif pctl <= COMPRESSION_PCTL:
        volatility = COMPRESSION
    else:
        volatility = NORMAL

    return Regime(trend=trend, volatility=volatility, confidence=round(confidence, 2), vol_percentile=round(pctl, 2))
