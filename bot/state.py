"""Signal state persistence so we only email when a decision changes.

State lives in state/last_signals.json and is committed back to the repo by
the GitHub Actions workflow, surviving between scheduled runs.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

STATE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "state", "last_signals.json")


def load_state(path: str = STATE_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def get_stance(state: dict, symbol: str) -> str:
    return state.get(symbol, {}).get("stance", "FLAT")


def set_stance(state: dict, symbol: str, stance: str, price: float) -> None:
    prev = state.get(symbol, {})
    entry = {
        "stance": stance,
        "price": price,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if stance != prev.get("stance"):
        entry["since"] = entry["updated_at"]
    else:
        entry["since"] = prev.get("since", entry["updated_at"])
    state[symbol] = entry
