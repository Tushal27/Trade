"""Open-trade tracking: watch each signal's stop/target and keep a ledger.

When a signal opens, its plan (entry/stop/target) is stored in the state
file. Every run, open positions are checked against the 1h bars printed
since entry; a touch of the stop or target closes the trade, fires an alert,
and appends the outcome to state/ledger.json.

Convention: if one bar touches both stop and target, the stop is assumed to
have been hit first (the conservative reading).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from .data import Candles

LEDGER_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "state", "ledger.json")

COOLDOWN_HOURS = 6  # no fresh entry on a symbol right after a stop-out

OUTCOME_TARGET = "TARGET_HIT"
OUTCOME_STOP = "STOP_HIT"
OUTCOME_SIGNAL_EXIT = "SIGNAL_EXIT"


def load_ledger(path: str = LEDGER_PATH) -> list[dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def append_ledger(trade: dict, path: str = LEDGER_PATH) -> None:
    ledger = load_ledger(path)
    ledger.append(trade)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=2)
        fh.write("\n")


def r_multiple(side: str, entry: float, stop: float, exit_price: float) -> float:
    risk = abs(entry - stop)
    if risk == 0:
        return 0.0
    pnl = (exit_price - entry) if side == "LONG" else (entry - exit_price)
    return pnl / risk


def check_hit(side: str, entry_ms: int, stop: float, target: float, ltf: Candles) -> tuple[str, float] | None:
    """Scan closed bars after entry for a stop/target touch.

    Returns (outcome, exit_price) or None if the trade is still open.
    """
    for i, t in enumerate(ltf.open_times):
        if t <= entry_ms:
            continue
        high, low = ltf.highs[i], ltf.lows[i]
        if side == "LONG":
            if low <= stop:
                return OUTCOME_STOP, stop
            if high >= target:
                return OUTCOME_TARGET, target
        else:
            if high >= stop:
                return OUTCOME_STOP, stop
            if low <= target:
                return OUTCOME_TARGET, target
    return None


def close_trade(symbol: str, pos: dict, outcome: str, exit_price: float) -> dict:
    """Build the ledger record for a finished trade."""
    side = pos["stance"]
    entry = float(pos["entry"])
    stop = float(pos["stop"])
    record = {
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": pos.get("target"),
        "exit_price": exit_price,
        "outcome": outcome,
        "r_multiple": round(r_multiple(side, entry, stop, exit_price), 2),
        "opened_at": pos.get("since"),
        "closed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    append_ledger(record)
    return record


def cooldown_until_iso(hours: int = COOLDOWN_HOURS) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat(timespec="seconds")


def in_cooldown(pos: dict) -> str | None:
    """Returns a veto string if the symbol is inside its post-stop cooldown."""
    iso = pos.get("cooldown_until")
    if not iso:
        return None
    try:
        until = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if datetime.now(timezone.utc) < until:
        return f"Cooldown veto: recent stop-out, no re-entry until {iso}."
    return None
