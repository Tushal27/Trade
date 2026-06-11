"""Optional AI analyst — "the Brain": plain-language commentary on alerts.

Design rule: the Brain NEVER makes, modifies, or vetoes trade decisions.
The backtested rules in strategy.py keep sole authority. An AI's market
hunches cannot be validated the way the rules were (a model that has read
market history would ace any backtest from memory — look-ahead bias), so it
is wired as an analyst, not a decider, preserving the evidence loop.

Two interchangeable providers, used in this order of preference:
  ANTHROPIC_API_KEY  -> Claude (premium quality, ~1 cent per commentary)
  GEMINI_API_KEY     -> Google Gemini (free tier — costs nothing)
With neither key present, alerts simply go out without commentary.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

try:
    import anthropic
except ImportError:  # package not installed — Claude path off, Gemini still works
    anthropic = None

DEFAULT_CLAUDE_MODEL = "claude-opus-4-8"   # override with the BRAIN_MODEL env var
DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"  # override with the GEMINI_MODEL env var

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


def _claude_configured() -> bool:
    return anthropic is not None and bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _gemini_configured() -> bool:
    return bool(os.environ.get("GEMINI_API_KEY", "").strip())


def brain_configured() -> bool:
    return _claude_configured() or _gemini_configured()


def _claude_commentary(report: str) -> str | None:
    client = anthropic.Anthropic()
    model = os.environ.get("BRAIN_MODEL", "").strip() or DEFAULT_CLAUDE_MODEL
    response = client.messages.create(
        model=model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": f"Today's signal report:\n\n{report}"}],
    )
    if response.stop_reason == "refusal":
        return None
    return "".join(b.text for b in response.content if b.type == "text").strip() or None


def _gemini_commentary(report: str) -> str | None:
    key = os.environ["GEMINI_API_KEY"].strip()
    model = os.environ.get("GEMINI_MODEL", "").strip() or DEFAULT_GEMINI_MODEL
    payload = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": f"Today's signal report:\n\n{report}"}]}],
        "generationConfig": {"maxOutputTokens": 1024},
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=payload,
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        body = json.loads(resp.read())
    parts = body["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts).strip() or None


def commentary(report: str) -> str | None:
    """Return the Brain's read on a signal report, or None (never raises)."""
    try:
        if _claude_configured():
            return _claude_commentary(report)
        if _gemini_configured():
            return _gemini_commentary(report)
        return None
    except Exception as err:  # commentary is a bonus — never block the alert
        print(f"[WARN] Brain commentary unavailable: {err}", file=sys.stderr)
        return None
