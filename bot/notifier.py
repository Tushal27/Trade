"""Notification layer: Gmail (primary) and optional Telegram.

Environment variables (set as GitHub Actions secrets):
  GMAIL_ADDRESS        the Gmail account that sends the mail
  GMAIL_APP_PASSWORD   a Google App Password (NOT the normal account password)
Optional:
  RECIPIENT_EMAIL      defaults to GMAIL_ADDRESS (send to yourself)
  TELEGRAM_BOT_TOKEN   token from @BotFather — enables Telegram alerts
  TELEGRAM_CHAT_ID     your numeric Telegram chat id
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.error
import urllib.request
from email.mime.text import MIMEText


class NotifyError(Exception):
    pass


def send_email(subject: str, body: str) -> None:
    sender = os.environ.get("GMAIL_ADDRESS", "").strip()
    password = os.environ.get("GMAIL_APP_PASSWORD", "").strip()
    recipient = os.environ.get("RECIPIENT_EMAIL", "").strip() or sender

    if not sender or not password:
        raise NotifyError(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD not set. "
            "Add them as repository secrets (see README)."
        )

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    context = ssl.create_default_context()
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context, timeout=30) as server:
            server.login(sender, password)
            server.sendmail(sender, [recipient], msg.as_string())
    except smtplib.SMTPAuthenticationError as err:
        raise NotifyError(
            "Gmail rejected the login. Make sure 2-Step Verification is ON and "
            "you are using an App Password, not your normal password."
        ) from err
    except (smtplib.SMTPException, OSError) as err:
        raise NotifyError(f"failed to send email: {err}") from err


def telegram_configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
                and os.environ.get("TELEGRAM_CHAT_ID", "").strip())


def send_telegram(text: str) -> None:
    """Send a plain-text message via the Telegram Bot API (free, official)."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise NotifyError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set.")

    payload = json.dumps({
        "chat_id": chat_id,
        "text": text[:4096],  # Telegram message size limit
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
        if not body.get("ok"):
            raise NotifyError(f"Telegram API error: {body}")
    except urllib.error.HTTPError as err:
        detail = ""
        try:
            detail = json.loads(err.read()).get("description", "")
        except Exception:
            pass
        if err.code == 401:
            raise NotifyError("Telegram rejected the bot token — re-check TELEGRAM_BOT_TOKEN.") from err
        if err.code == 400 and "chat not found" in detail.lower():
            raise NotifyError(
                "Telegram says 'chat not found' — re-check TELEGRAM_CHAT_ID, and make sure "
                "you pressed Start on your bot in Telegram first."
            ) from err
        raise NotifyError(f"Telegram send failed ({err.code}): {detail or err}") from err
    except (urllib.error.URLError, OSError) as err:
        raise NotifyError(f"Telegram send failed: {err}") from err
