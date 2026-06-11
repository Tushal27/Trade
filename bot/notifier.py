"""Gmail notification layer.

Requires two environment variables (set as GitHub Actions secrets):
  GMAIL_ADDRESS       the Gmail account that sends the mail
  GMAIL_APP_PASSWORD  a Google App Password (NOT the normal account password)
Optional:
  RECIPIENT_EMAIL     defaults to GMAIL_ADDRESS (send to yourself)
"""

from __future__ import annotations

import os
import smtplib
import ssl
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
