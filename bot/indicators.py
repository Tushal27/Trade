"""Pure-python technical indicators. No third-party math dependencies.

All functions take plain lists of floats (oldest first) and return either a
single float (latest value) or a full series aligned to the input.
"""

from __future__ import annotations

import math


def sma(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need {period} values, got {len(values)}")
    return sum(values[-period:]) / period


def ema_series(values: list[float], period: int) -> list[float]:
    """Full EMA series seeded with the SMA of the first `period` values."""
    if len(values) < period:
        raise ValueError(f"need {period} values, got {len(values)}")
    k = 2.0 / (period + 1)
    out: list[float] = []
    seed = sum(values[:period]) / period
    prev = seed
    for i, v in enumerate(values):
        if i < period:
            out.append(seed)
            continue
        prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def ema(values: list[float], period: int) -> float:
    return ema_series(values, period)[-1]


def rsi(closes: list[float], period: int = 14) -> float:
    """Wilder's RSI on the latest bar."""
    if len(closes) < period + 1:
        raise ValueError(f"need {period + 1} closes, got {len(closes)}")
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta >= 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    """Wilder's Average True Range on the latest bar."""
    n = len(closes)
    if n < period + 1:
        raise ValueError(f"need {period + 1} bars, got {n}")
    trs: list[float] = []
    for i in range(1, n):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def bollinger(closes: list[float], period: int = 20, num_std: float = 2.0) -> tuple[float, float, float]:
    """Returns (lower, middle, upper) bands for the latest bar."""
    window = closes[-period:]
    if len(window) < period:
        raise ValueError(f"need {period} closes, got {len(closes)}")
    mid = sum(window) / period
    var = sum((c - mid) ** 2 for c in window) / period
    std = math.sqrt(var)
    return mid - num_std * std, mid, mid + num_std * std


def realized_vol(closes: list[float], period: int = 20) -> float:
    """Stdev of log returns over the trailing window (per-bar, not annualized)."""
    if len(closes) < period + 1:
        raise ValueError(f"need {period + 1} closes, got {len(closes)}")
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(len(closes) - period, len(closes))]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / len(rets)
    return math.sqrt(var)


def percentile_rank(history: list[float], value: float) -> float:
    """Fraction of historical values that are <= value, in [0, 1]."""
    if not history:
        return 0.5
    return sum(1 for h in history if h <= value) / len(history)
