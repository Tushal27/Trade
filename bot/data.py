"""Market data layer: Binance public klines with host fallback and validation.

Uses only public market-data endpoints — no API key or exchange account.
`data-api.binance.vision` is tried first because it serves public market data
from regions where `api.binance.com` is geo-blocked (e.g., US-hosted CI runners).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

HOSTS = [
    "https://data-api.binance.vision",
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
]

INTERVAL_MS = {
    "15m": 15 * 60 * 1000,
    "1h": 60 * 60 * 1000,
    "4h": 4 * 60 * 60 * 1000,
    "1d": 24 * 60 * 60 * 1000,
}


class DataError(Exception):
    """Raised when market data cannot be fetched or fails validation."""


@dataclass
class Candles:
    symbol: str
    interval: str
    open_times: list[int]
    opens: list[float]
    highs: list[float]
    lows: list[float]
    closes: list[float]
    volumes: list[float]

    def __len__(self) -> int:
        return len(self.closes)

    @property
    def last_close(self) -> float:
        return self.closes[-1]


def _http_get(url: str, timeout: float = 10.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "trade-signal-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_klines(symbol: str, interval: str, limit: int = 300, retries: int = 2) -> Candles:
    """Fetch closed klines for a symbol, trying each host with retries.

    The still-forming last candle is dropped so signals only use closed bars.
    """
    path = f"/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        for host in HOSTS:
            try:
                raw = _http_get(host + path)
                return _parse_and_validate(symbol, interval, json.loads(raw))
            except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError, DataError) as err:
                last_err = err
        if attempt < retries:
            time.sleep(2 ** (attempt + 1))
    raise DataError(f"all hosts failed for {symbol} {interval}: {last_err}")


def _parse_and_validate(symbol: str, interval: str, rows: list) -> Candles:
    if not isinstance(rows, list) or len(rows) < 50:
        raise DataError(f"{symbol} {interval}: bad or short payload ({len(rows) if isinstance(rows, list) else type(rows)})")

    candles = Candles(
        symbol=symbol,
        interval=interval,
        open_times=[int(r[0]) for r in rows],
        opens=[float(r[1]) for r in rows],
        highs=[float(r[2]) for r in rows],
        lows=[float(r[3]) for r in rows],
        closes=[float(r[4]) for r in rows],
        volumes=[float(r[5]) for r in rows],
    )

    interval_ms = INTERVAL_MS[interval]
    now_ms = int(time.time() * 1000)
    # Drop the in-progress candle: its close is not final.
    if candles.open_times[-1] + interval_ms > now_ms:
        for field in ("open_times", "opens", "highs", "lows", "closes", "volumes"):
            getattr(candles, field).pop()

    if len(candles) < 50:
        raise DataError(f"{symbol} {interval}: not enough closed candles")
    if any(c <= 0 for c in candles.closes):
        raise DataError(f"{symbol} {interval}: non-positive price in feed")
    if any(candles.open_times[i] >= candles.open_times[i + 1] for i in range(len(candles) - 1)):
        raise DataError(f"{symbol} {interval}: timestamps not increasing")
    # Staleness: newest closed candle must have closed within the last 2 intervals.
    if now_ms - (candles.open_times[-1] + interval_ms) > 2 * interval_ms:
        raise DataError(f"{symbol} {interval}: feed is stale")
    return candles
