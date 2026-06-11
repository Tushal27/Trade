"""Entry-veto filters: information the price chart alone cannot see.

These never *create* signals — they only block statistically weak entries.
Every filter degrades gracefully: if its data source is unreachable, the
filter is skipped (logged) rather than blocking trading on missing data.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

# Funding beyond ±0.05% per 8h (~±55%/yr) marks a crowded, fragile trade.
FUNDING_VETO_THRESHOLD = 0.0005

OKX_INST = {"BTCUSDT": "BTC-USDT-SWAP", "ETHUSDT": "ETH-USDT-SWAP"}


def _get_json(url: str, timeout: float = 10.0):
    req = urllib.request.Request(url, headers={"User-Agent": "trade-signal-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def fetch_funding_rate(symbol: str) -> float | None:
    """Current perp funding rate (per 8h period) from the first source that
    answers: Binance futures -> Bybit -> OKX. None if all are unreachable."""
    sources = [
        (f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={symbol}",
         lambda d: float(d["lastFundingRate"])),
        (f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}",
         lambda d: float(d["result"]["list"][0]["fundingRate"])),
        (f"https://www.okx.com/api/v5/public/funding-rate?instId={OKX_INST.get(symbol, '')}",
         lambda d: float(d["data"][0]["fundingRate"])),
    ]
    for url, extract in sources:
        try:
            return extract(_get_json(url))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                ValueError, KeyError, IndexError, TypeError):
            continue
    return None


def funding_veto(symbol: str, side: str, rate: float | None) -> str | None:
    """Veto entries in the crowded direction when funding is extreme.

    Positive funding = longs pay shorts = crowded longs (fragile to squeezes);
    extreme negative = crowded shorts. A None rate (all sources down) skips
    the filter rather than blocking trading on missing data.
    """
    if rate is None:
        print(f"[INFO] {symbol}: funding rate unavailable — filter skipped.", file=sys.stderr)
        return None
    if side == "LONG" and rate > FUNDING_VETO_THRESHOLD:
        return (f"Funding veto: longs paying {rate * 100:.3f}%/8h — crowded long, "
                f"squeeze risk too high.")
    if side == "SHORT" and rate < -FUNDING_VETO_THRESHOLD:
        return (f"Funding veto: shorts paying {abs(rate) * 100:.3f}%/8h — crowded short, "
                f"squeeze risk too high.")
    return None


def btc_trend_veto(symbol: str, side: str, btc_trend: str | None) -> str | None:
    """Alts follow BTC: veto alt entries that fight the BTC 4h trend."""
    if symbol == "BTCUSDT" or btc_trend is None:
        return None
    if side == "LONG" and btc_trend == "TREND_DOWN":
        return "BTC veto: BTC is in a 4h downtrend — alt longs against BTC are statistically weak."
    if side == "SHORT" and btc_trend == "TREND_UP":
        return "BTC veto: BTC is in a 4h uptrend — alt shorts against BTC are statistically weak."
    return None
