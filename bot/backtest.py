"""Walk-forward backtest: replay history through the LIVE signal code.

This runs the exact same detect_regime() and decide() functions the bot uses
in production — no separate "backtest strategy" that could quietly diverge.

Conservative conventions (results are biased against us, on purpose):
  - entries fill at the signal bar's close
  - if one bar touches both stop and target, the stop wins
  - fees + slippage of 0.16% round-trip are charged on every trade
  - funding-rate filter is NOT simulated (no reliable free history), so live
    results should be slightly better-filtered than the backtest

Usage (needs network access to Binance):
  python -m bot.backtest                          # BTC+ETH, last 365 days
  python -m bot.backtest --days 730               # two years
  python -m bot.backtest --symbols BTCUSDT --email # email the report too
"""

from __future__ import annotations

import argparse
import bisect
import json
import time
import urllib.request

from .data import Candles, DataError, _parse_and_validate  # reuse validation
from .data import HOSTS
from .regime import TREND_DOWN, TREND_UP, detect_regime
from .strategy import BASELINE, CANDIDATE, FLAT, LONG, RIDE, SHORT, TREND_ONLY, Params, decide
from .tracker import r_multiple

FEE_ROUND_TRIP = 0.0016   # taker fees + slippage, as a fraction of price
WARMUP_1H = 300           # bars needed before the first decision
WARMUP_4H = 260           # bars needed for EMA200 + vol percentile history
COOLDOWN_BARS = 6         # 1h bars without re-entry after a stop-out


def fetch_klines_range(symbol: str, interval: str, start_ms: int, end_ms: int) -> Candles:
    """Paginated historical fetch (Binance caps each request at 1000 rows)."""
    interval_ms = {"1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}[interval]
    rows: list = []
    cursor = start_ms
    while cursor < end_ms:
        path = (f"/api/v3/klines?symbol={symbol}&interval={interval}"
                f"&startTime={cursor}&limit=1000")
        batch = None
        last_err: Exception | None = None
        for host in HOSTS:
            try:
                req = urllib.request.Request(host + path, headers={"User-Agent": "trade-signal-bot/1.0"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    batch = json.loads(resp.read())
                break
            except Exception as err:  # try next host
                last_err = err
        if batch is None:
            raise DataError(f"history fetch failed for {symbol} {interval}: {last_err}")
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1][0]) + interval_ms
        time.sleep(0.15)  # stay polite to the public API
    return _parse_and_validate(symbol, interval, rows)


def slice_candles(c: Candles, end_idx: int, lookback: int) -> Candles:
    """Candles[end_idx-lookback+1 .. end_idx] as a new view object."""
    lo = max(0, end_idx + 1 - lookback)
    hi = end_idx + 1
    return Candles(c.symbol, c.interval, c.open_times[lo:hi], c.opens[lo:hi],
                   c.highs[lo:hi], c.lows[lo:hi], c.closes[lo:hi], c.volumes[lo:hi])


def simulate(htf: Candles, ltf: Candles, btc_htf: Candles | None = None,
             params: Params | None = None) -> dict:
    """Walk the 1h series bar by bar; returns stats + trade list.

    btc_htf enables the BTC-trend veto for alt symbols (pass None for BTC).
    """
    params = params or BASELINE
    # Precompute the regime after each closed 4h bar (and BTC's trend for the veto).
    regimes, regime_times = [], []
    for j in range(WARMUP_4H, len(htf)):
        regimes.append(detect_regime(slice_candles(htf, j, 400)))
        regime_times.append(htf.open_times[j] + 14_400_000)  # usable once the bar closes
    btc_trends, btc_times = [], []
    if btc_htf is not None:
        for j in range(WARMUP_4H, len(btc_htf)):
            btc_trends.append(detect_regime(slice_candles(btc_htf, j, 400)).trend)
            btc_times.append(btc_htf.open_times[j] + 14_400_000)

    trades: list[dict] = []
    stance = FLAT
    entry = stop = 0.0
    target: float | None = 0.0
    tag = "trend"
    vol_tag = "NORMAL"
    cooldown_left = 0

    for i in range(WARMUP_1H, len(ltf)):
        bar_close_t = ltf.open_times[i] + 3_600_000
        k = bisect.bisect_right(regime_times, bar_close_t) - 1
        if k < 0:
            continue
        regime = regimes[k]

        high, low, close = ltf.highs[i], ltf.lows[i], ltf.closes[i]

        # Manage the open position first (stop checked before target).
        if stance in (LONG, SHORT):
            hit = None
            if stance == LONG and low <= stop:
                hit = ("STOP_HIT", stop)
            elif stance == LONG and target is not None and high >= target:
                hit = ("TARGET_HIT", target)
            elif stance == SHORT and high >= stop:
                hit = ("STOP_HIT", stop)
            elif stance == SHORT and target is not None and low <= target:
                hit = ("TARGET_HIT", target)
            if hit:
                outcome, exit_price = hit
                trades.append(_record(stance, entry, stop, exit_price, outcome, tag, vol_tag))
                cooldown_left = _cooldown_for(params, outcome)
                stance = FLAT

        if cooldown_left > 0:
            cooldown_left -= 1
            veto = ["cooldown"]
        else:
            veto = []
        window = slice_candles(ltf, i, WARMUP_1H)
        d = decide(ltf.symbol, regime, window, stance, entry_vetoes=veto or None, params=params)

        # BTC-trend veto, applied after decide() so only the side that fights
        # BTC's trend is blocked (matches the live filter's behavior).
        if btc_htf is not None and d.stance in (LONG, SHORT) and stance == FLAT:
            kb = bisect.bisect_right(btc_times, bar_close_t) - 1
            if kb >= 0:
                bt = btc_trends[kb]
                if (d.stance == LONG and bt == TREND_DOWN) or (d.stance == SHORT and bt == TREND_UP):
                    d.stance = FLAT

        if stance in (LONG, SHORT) and d.stance != stance:
            # Signal exit (trend break / mean reached) at the bar close.
            trades.append(_record(stance, entry, stop, close, "SIGNAL_EXIT", tag, vol_tag))
            cooldown_left = _cooldown_for(params, "SIGNAL_EXIT")
            stance = FLAT
        if stance == FLAT and d.stance in (LONG, SHORT) and d.stop is not None:
            stance, entry, stop, target = d.stance, close, d.stop, d.target
            tag = "trend" if regime.trend in (TREND_UP, TREND_DOWN) else "range"
            vol_tag = regime.volatility

    return _stats(ltf.symbol, trades)


def _cooldown_for(params: Params, outcome: str) -> int:
    """Baseline: cooldown after stop-outs only (matches the original live
    behavior). Candidate: cool down after every close to kill re-entry churn."""
    if outcome == "STOP_HIT":
        return COOLDOWN_BARS * 2 if params.trail else COOLDOWN_BARS
    return COOLDOWN_BARS if params.trail else 0


def _record(side: str, entry: float, stop: float, exit_price: float, outcome: str,
            tag: str, vol_tag: str = "NORMAL") -> dict:
    r = r_multiple(side, entry, stop, exit_price)
    risk = abs(entry - stop)
    fee_r = (FEE_ROUND_TRIP * entry / risk) if risk > 0 else 0.0
    return {"side": side, "entry": entry, "stop": stop, "exit": exit_price,
            "outcome": outcome, "tag": tag, "vol": vol_tag, "r": round(r - fee_r, 3)}


def _stats(symbol: str, trades: list[dict]) -> dict:
    rs = [t["r"] for t in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    cumulative, peak, max_dd = 0.0, 0.0, 0.0
    for r in rs:
        cumulative += r
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    def bucket(field: str, value: str) -> str:
        sub = [t["r"] for t in trades if t.get(field) == value]
        if not sub:
            return "no trades"
        return f"{len(sub)} trades, {sum(sub):+.1f}R"

    return {
        "symbol": symbol,
        "trades": len(rs),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(rs), 3) if rs else 0.0,
        "avg_r": round(sum(rs) / len(rs), 3) if rs else 0.0,
        "total_r": round(sum(rs), 2),
        "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else float("inf") if wins else 0.0,
        "max_drawdown_r": round(max_dd, 2),
        "trend_bucket": bucket("tag", "trend"),
        "range_bucket": bucket("tag", "range"),
        "long_bucket": bucket("side", "LONG"),
        "short_bucket": bucket("side", "SHORT"),
        "vol_buckets": {v: bucket("vol", v) for v in ("COMPRESSION", "NORMAL", "EXPANSION")},
        "trade_list": trades,
    }


def format_report(sections: list[tuple[str, list[dict]]], days: int) -> str:
    lines = [f"Backtest report — last {days} days, fees 0.16%/trade, conservative fills", ""]
    for label, results in sections:
        lines.append(f"=== {label} ===")
        for s in results:
            lines += [
                f"{s['symbol']}:",
                f"  Trades:        {s['trades']}  ({s['wins']} wins / {s['losses']} losses)",
                f"  Win rate:      {s['win_rate']:.0%}",
                f"  Avg R/trade:   {s['avg_r']:+.2f}",
                f"  Total R:       {s['total_r']:+.2f}   (at 1% risk per trade ≈ {s['total_r']:+.1f}% on capital)",
                f"  Profit factor: {s['profit_factor']}",
                f"  Max drawdown:  {s['max_drawdown_r']:.2f}R",
                f"  Trend trades:  {s['trend_bucket']}",
                f"  Range trades:  {s['range_bucket']}",
                f"  Long trades:   {s['long_bucket']}",
                f"  Short trades:  {s['short_bucket']}",
                f"  By volatility: " + "  |  ".join(f"{k}: {v}" for k, v in s['vol_buckets'].items()),
                "",
            ]
    lines.append("Reading guide: win rates of 20-50% are NORMAL for this strategy class —")
    lines.append("profitability comes from winners being bigger than losers (avg R > 0).")
    lines.append("Past performance does not guarantee future results.")
    return "\n".join(lines)


VARIANTS = {"baseline": BASELINE, "candidate": CANDIDATE, "trend_only": TREND_ONLY, "ride": RIDE}


def main() -> int:
    parser = argparse.ArgumentParser(description="Walk-forward backtest of the live signal code")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT"])
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--variant", choices=[*VARIANTS, "compare"], default="compare")
    parser.add_argument("--email", action="store_true", help="also send the report via configured channels")
    args = parser.parse_args()

    end_ms = int(time.time() * 1000)
    # Extra history before the window so warmup bars don't eat the test period.
    start_1h = end_ms - (args.days * 24 + WARMUP_1H) * 3_600_000
    start_4h = end_ms - (args.days * 6 + WARMUP_4H + 40) * 14_400_000

    # Fetch once, simulate every requested variant on the same data.
    data: dict[str, tuple[Candles, Candles]] = {}
    btc_htf = None
    for symbol in args.symbols:
        print(f"Fetching {symbol} history…")
        htf = fetch_klines_range(symbol, "4h", start_4h, end_ms)
        ltf = fetch_klines_range(symbol, "1h", start_1h, end_ms)
        data[symbol] = (htf, ltf)
        if symbol == "BTCUSDT":
            btc_htf = htf

    chosen = VARIANTS if args.variant == "compare" else {args.variant: VARIANTS[args.variant]}
    sections = []
    for label, params in chosen.items():
        results = []
        for symbol in args.symbols:
            htf, ltf = data[symbol]
            use_btc = btc_htf if symbol != "BTCUSDT" else None
            print(f"Simulating {symbol} [{label}]…")
            results.append(simulate(htf, ltf, use_btc, params=params))
        sections.append((label.upper(), results))

    report = format_report(sections, args.days)
    print("\n" + report)

    if args.email:
        from .main import dispatch
        dispatch(f"🧪 Backtest Report — {args.days} days ({args.variant})", report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
