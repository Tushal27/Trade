"""Optional AI analyst — "the Brain": plain-language commentary on alerts.

Design rule: the Brain NEVER makes, modifies, or vetoes trade decisions.
The backtested rules in strategy.py keep sole authority. An AI's market
hunches cannot be validated the way the rules were (a model that has read
market history would ace any backtest from memory — look-ahead bias), so it
is wired as an analyst, not a decider, preserving the evidence loop.

Enabled by setting the ANTHROPIC_API_KEY repository secret; when the key or
the `anthropic` package is absent, alerts simply go out without commentary.
"""

from __future__ import annotations

import os
import sys

try:
    import anthropic
except ImportError:  # package not installed — Brain stays off, bot unaffected
    anthropic = None

DEFAULT_MODEL = "claude-opus-4-8"  # override with the BRAIN_MODEL env var

SYSTEM_PROMPT = """You are the analyst companion ("the Brain") inside an automated crypto trade-signal bot.
The bot's statistical rules have ALREADY made the trading decision shown to you — your job is
commentary, never to second-guess, override, or veto the signal, and never to predict prices.

The reader is a retail trader, not a professional. Write for them.

Given the bot's signal report, write a short market read:
- What the current regime and signal mean in plain language.
- The one or two key risks around this specific situation (e.g. crowded positioning,
  thin weekend liquidity, nearby volatility, what would invalidate the setup).
- One sentence of discipline coaching if relevant (position sizing, respecting the stop,
  not revenge trading).

Rules: under 130 words. Plain sentences, no headers or bullet lists, no hype, no emojis.
Never say the trade "will" win or lose. Never suggest ignoring or adjusting the bot's
stop, target, or decision."""


def brain_configured() -> bool:
    return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def commentary(report: str) -> str | None:
    """Return the Brain's read on a signal report, or None (never raises)."""
    if not brain_configured():
        return None
    try:
        client = anthropic.Anthropic()
        model = os.environ.get("BRAIN_MODEL", "").strip() or DEFAULT_MODEL
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": f"Today's signal report:\n\n{report}"}],
        )
        if response.stop_reason == "refusal":
            return None
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text or None
    except Exception as err:  # commentary is a bonus — never block the alert
        print(f"[WARN] Brain commentary unavailable: {err}", file=sys.stderr)
        return None
