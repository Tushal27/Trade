"""Entry point: fetch data, detect regime, decide, and email on signal changes.

Usage:
  python -m bot.main                 # normal run (used by GitHub Actions)
  python -m bot.main --dry-run       # no email, no state write; prints decisions
  python -m bot.main --force-email   # email the current status even if unchanged
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone

from .data import DataError, fetch_klines
from .notifier import NotifyError, send_email, send_telegram, telegram_configured
from .regime import detect_regime
from .state import get_stance, load_state, save_state, set_stance
from .strategy import FLAT, decide

SYMBOLS = ["BTCUSDT", "ETHUSDT"]
HTF_INTERVAL = "4h"   # regime timeframe
LTF_INTERVAL = "1h"   # signal timeframe

DISCLAIMER = (
    "Automated technical signal for information only — not financial advice. "
    "Crypto is highly volatile; never risk money you cannot afford to lose."
)


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
    if d.stop is not None and d.target is not None:
        rr = abs(d.target - d.price) / abs(d.price - d.stop) if d.price != d.stop else 0
        lines.append(f"  Stop:    {d.stop:,.2f}")
        lines.append(f"  Target:  {d.target:,.2f}  (~{rr:.1f}R)")
    for reason in d.reasons:
        lines.append(f"  Why:     {reason}")
    return "\n".join(lines)


def run(dry_run: bool = False, force_email: bool = False) -> int:
    state = load_state()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    changes: list[tuple] = []   # (symbol, prev, decision)
    blocks: list[str] = []
    failures: list[str] = []

    for symbol in SYMBOLS:
        prev = get_stance(state, symbol)
        try:
            htf = fetch_klines(symbol, HTF_INTERVAL, limit=400)
            ltf = fetch_klines(symbol, LTF_INTERVAL, limit=300)
        except DataError as err:
            failures.append(f"{symbol}: {err}")
            print(f"[WARN] {symbol}: data fetch failed — keeping previous state. {err}", file=sys.stderr)
            continue

        regime = detect_regime(htf)
        d = decide(symbol, regime, ltf, prev)
        blocks.append(format_decision_block(d, prev))

        if d.stance != prev:
            changes.append((symbol, prev, d))
        if not dry_run:
            set_stance(state, symbol, d.stance, d.price)

    report = f"Trade-signal check — {now}\n\n" + "\n\n".join(blocks)
    if failures:
        report += "\n\nData issues:\n  " + "\n  ".join(failures)
    print(report)

    if dry_run:
        print("\n[dry-run] no email sent, state untouched.")
        return 0 if blocks else 1

    save_state(state)

    if changes:
        first_sym, first_prev, first_d = changes[0]
        subject = "🚨 Trade Signal: " + transition_headline(first_sym, first_prev, first_d.stance, first_d.price)
        if len(changes) > 1:
            subject += f" (+{len(changes) - 1} more)"
        body = report + "\n\n" + DISCLAIMER
        if not dispatch(subject, body):
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
    parser.add_argument("--dry-run", action="store_true", help="print decisions; no email, no state write")
    parser.add_argument("--force-email", action="store_true", help="send a status email even without changes")
    args = parser.parse_args()
    force = args.force_email or os.environ.get("FORCE_EMAIL", "").lower() == "true"
    return run(dry_run=args.dry_run, force_email=force)


if __name__ == "__main__":
    raise SystemExit(main())
