"""Weekly performance scorecard built from the live trade ledger.

Run by a Monday cron in GitHub Actions:
  python -m bot.report           # email/telegram the scorecard
  python -m bot.report --print   # just print it
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

from .tracker import load_ledger

OUTCOME_LABEL = {"TARGET_HIT": "🎯 target", "STOP_HIT": "🛑 stop", "SIGNAL_EXIT": "↩ signal exit"}


def summarize(trades: list[dict], title: str) -> str:
    if not trades:
        return f"{title}: no closed trades."
    rs = [float(t.get("r_multiple", 0.0)) for t in trades]
    wins = [r for r in rs if r > 0]
    lines = [
        f"{title}:",
        f"  Closed trades: {len(rs)}",
        f"  Win rate:      {len(wins) / len(rs):.0%}",
        f"  Total result:  {sum(rs):+.2f}R   (at 1% risk per trade ≈ {sum(rs):+.1f}% on capital)",
        f"  Average:       {sum(rs) / len(rs):+.2f}R per trade",
    ]
    return "\n".join(lines)


def build_report() -> str:
    ledger = load_ledger()
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    def closed_after(t: dict, cutoff: datetime) -> bool:
        try:
            return datetime.fromisoformat(t["closed_at"]) >= cutoff
        except (KeyError, ValueError):
            return False

    this_week = [t for t in ledger if closed_after(t, week_ago)]

    parts = [f"Weekly scorecard — {now.strftime('%Y-%m-%d')}",
             "",
             summarize(this_week, "Last 7 days"),
             "",
             summarize(ledger, "All time")]

    if this_week:
        parts += ["", "This week's trades:"]
        for t in this_week:
            label = OUTCOME_LABEL.get(t.get("outcome", ""), t.get("outcome", "?"))
            parts.append(f"  {t['side']} {t['symbol']}: {t['entry']:,.2f} -> "
                         f"{t['exit_price']:,.2f}  {t.get('r_multiple', 0):+.2f}R  ({label})")

    parts += ["",
              "Remember: 35-50% win rates are normal for trend systems — the math",
              "works through winners outsizing losers, not through accuracy."]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly performance scorecard")
    parser.add_argument("--print", dest="print_only", action="store_true",
                        help="print the scorecard without sending it")
    args = parser.parse_args()

    report = build_report()
    print(report)
    if args.print_only:
        return 0

    from .main import dispatch
    return 0 if dispatch("📈 Weekly Trading Scorecard", report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
