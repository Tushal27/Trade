"""Entry point: fetch data, manage open trades, detect regime, decide,
and notify (Gmail + optional Telegram) on signal changes.

Usage:
  python -m bot.main                 # normal run (used by GitHub Actions)
  python -m bot.main --dry-run       # no notifications, no state write
  python -m bot.main --force-email   # send current status even if unchanged
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from .data import DataError, fetch_klines
from .filters import btc_trend_veto, fetch_funding_rate, funding_veto
from .notifier import NotifyError, send_email, send_telegram, telegram_configured
from .regime import detect_regime
from .state import get_stance, load_state, save_state, set_stance
from .strategy import BASELINE, FLAT, LONG, SHORT, decide
from .tracker import (OUTCOME_SIGNAL_EXIT, OUTCOME_STOP, check_hit, close_trade,
                      cooldown_until_iso, in_cooldown)

SYMBOLS = ["BTCUSDT", "ETHUSDT"]  # BTCUSDT must stay first: its regime gates alt entries
LIVE_PARAMS = BASELINE  # promote strategy.CANDIDATE only after the backtest proves it
HTF_INTERVAL = "4h"   # regime timeframe
LTF_INTERVAL = "1h"   # signal timeframe

DISCLAIMER = (
    "Automated technical signal for information only — not financial advice. "
    "Crypto is highly volatile; never risk money you cannot afford to lose."
)

OUTCOME_EMOJI = {OUTCOME_STOP: "🛑", "TARGET_HIT": "🎯"}


def dispatch(subject: str, body: str) -> bool:
    """Send to every configured channel; True if at least one delivered."""
    delivered = False
    try:
        send_email(subject, body)
        print(f"Email sent: {subject}")
        delivered = True
    except NotifyError as err:
        print(f"[ERROR] email: {err}", file=sys.stderr)
    if telegram_configured():
        try:
            send_telegram(f"{subject}\n\n{body}")
            print("Telegram alert sent.")
            delivered = True
        except NotifyError as err:
            print(f"[ERROR] telegram: {err}", file=sys.stderr)
    return delivered


def transition_headline(symbol: str, prev: str, new: str, price: float) -> str:
    if new != FLAT:
        return f"{new} {symbol} @ {price:,.2f}"
    return f"EXIT {prev} {symbol} @ {price:,.2f}"


def format_decision_block(d, prev_stance: str) -> str:
    lines = [
        f"{d.symbol}  —  {prev_stance} -> {d.stance}",
        f"  Price:   {d.price:,.2f}",
        f"  Regime:  {d.regime_label} (confidence {d.confidence:.0%})",
    ]
    if d.stop is not None:
        lines.append(f"  Stop:    {d.stop:,.2f}")
        if d.target is not None:
            rr = abs(d.target - d.price) / abs(d.price - d.stop) if d.price != d.stop else 0
            lines.append(f"  Target:  {d.target:,.2f}  (~{rr:.1f}R)")
        else:
            lines.append("  Target:  none — ride the trend; an EXIT alert will come when it breaks")
    for reason in d.reasons:
        lines.append(f"  Why:     {reason}")
    return "\n".join(lines)


def format_close_block(record: dict) -> str:
    emoji = OUTCOME_EMOJI.get(record["outcome"], "✅")
    return "\n".join([
        f"{emoji} {record['outcome'].replace('_', ' ')}: {record['side']} {record['symbol']}",
        f"  Entry:   {record['entry']:,.2f}",
        f"  Exit:    {record['exit_price']:,.2f}",
        f"  Result:  {record['r_multiple']:+.2f}R",
    ])


def run(dry_run: bool = False, force_email: bool = False) -> int:
    state = load_state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    changes: list[tuple] = []   # (symbol, prev, decision)
    closes: list[dict] = []     # ledger records closed this run
    blocks: list[str] = []
    failures: list[str] = []
    btc_trend: str | None = None

    for symbol in SYMBOLS:
        prev = get_stance(state, symbol)
        pos = state.get(symbol, {})
        try:
            htf = fetch_klines(symbol, HTF_INTERVAL, limit=400)
            ltf = fetch_klines(symbol, LTF_INTERVAL, limit=300)
        except DataError as err:
            failures.append(f"{symbol}: {err}")
            print(f"[WARN] {symbol}: data fetch failed — keeping previous state. {err}", file=sys.stderr)
            continue

        # 1) Manage an open trade: did price touch the stop or target?
        if prev in (LONG, SHORT) and pos.get("entry") is not None and pos.get("entry_ms") is not None:
            target = float(pos["target"]) if pos.get("target") is not None else None
            hit = check_hit(prev, int(pos["entry_ms"]), float(pos["stop"]), target, ltf)
            if hit:
                outcome, exit_price = hit
                if not dry_run:
                    record = close_trade(symbol, pos, outcome, exit_price)
                else:
                    record = {"symbol": symbol, "side": prev, "entry": pos["entry"],
                              "exit_price": exit_price, "outcome": outcome,
                              "r_multiple": 0.0}
                closes.append(record)
                blocks.append(format_close_block(record))
                cooldown = cooldown_until_iso() if outcome == OUTCOME_STOP else None
                if not dry_run:
                    set_stance(state, symbol, FLAT, exit_price, cooldown_until=cooldown)
                prev = FLAT
                pos = state.get(symbol, {})

        # 2) Regime + entry vetoes (filters never block exits).
        regime = detect_regime(htf)
        if symbol == "BTCUSDT":
            btc_trend = regime.trend

        vetoes: list[str] = []
        cooldown_veto = in_cooldown(pos)
        if cooldown_veto:
            vetoes.append(cooldown_veto)
        if prev == FLAT:  # funding lookup only matters for fresh entries
            rate = fetch_funding_rate(symbol)
            for side in (LONG, SHORT):
                v = funding_veto(symbol, side, rate)
                if v and v not in vetoes:
                    vetoes.append(v)
                v = btc_trend_veto(symbol, side, btc_trend)
                if v and v not in vetoes:
                    vetoes.append(v)

        d = decide(symbol, regime, ltf, prev, entry_vetoes=vetoes, params=LIVE_PARAMS)

        # A strategy exit (trend break / mean reached) also closes the ledger trade.
        if prev in (LONG, SHORT) and d.stance != prev and pos.get("entry") is not None:
            if not dry_run:
                record = close_trade(symbol, pos, OUTCOME_SIGNAL_EXIT, d.price)
                closes.append(record)
                blocks.append(format_close_block(record))

        blocks.append(format_decision_block(d, prev))

        if d.stance != prev:
            changes.append((symbol, prev, d))
        if not dry_run:
            plan = None
            if d.stance in (LONG, SHORT) and d.stop is not None:
                plan = {"entry": d.price, "stop": d.stop, "target": d.target,
                        "entry_ms": ltf.open_times[-1]}
            set_stance(state, symbol, d.stance, d.price, plan=plan)

    report = f"Trade-signal check — {now}\n\n" + "\n\n".join(blocks)
    if failures:
        report += "\n\nData issues:\n  " + "\n  ".join(failures)
    print(report)

    if dry_run:
        print("\n[dry-run] no notifications sent, state untouched.")
        return 0 if blocks else 1

    save_state(state)

    notify_due = bool(changes or closes)
    if notify_due:
        if closes:
            first = closes[0]
            emoji = OUTCOME_EMOJI.get(first["outcome"], "✅")
            subject = (f"{emoji} {first['outcome'].replace('_', ' ')}: "
                       f"{first['side']} {first['symbol']} {first['r_multiple']:+.2f}R")
        else:
            first_sym, first_prev, first_d = changes[0]
            subject = "🚨 Trade Signal: " + transition_headline(first_sym, first_prev, first_d.stance, first_d.price)
        extra = len(changes) + len(closes) - 1
        if extra > 0:
            subject += f" (+{extra} more)"
        if not dispatch(subject, report + "\n\n" + DISCLAIMER):
            return 1
    elif force_email:
        if not dispatch(f"📊 Trade Bot Status — {now}", report + "\n\n" + DISCLAIMER):
            return 1
    else:
        print("\nNo signal change — no notification.")

    # Fail the run only if we couldn't evaluate any symbol at all.
    return 0 if blocks else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Regime-aware crypto trade-signal bot")
    parser.add_argument("--dry-run", action="store_true", help="print decisions; no notifications, no state write")
    parser.add_argument("--force-email", action="store_true", help="send a status notification even without changes")
    args = parser.parse_args()
    force = args.force_email or os.environ.get("FORCE_EMAIL", "").lower() == "true"
    return run(dry_run=args.dry_run, force_email=force)


if __name__ == "__main__":
    raise SystemExit(main())
