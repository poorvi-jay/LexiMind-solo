"""
backend/services/mailer.py
Outgoing email, with a deliberate fallback for a machine that has none.

This project has no mail provider configured, and a password reset is useless if
the link never reaches anyone. So there are two paths:

  - **SMTP**, when SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD are set in the
    environment (backend/.env, which is gitignored). Nothing here ever reads a
    credential from anywhere else.
  - **The server log**, otherwise. The reset link is written to the backend
    console at WARNING so it can be copied during local development.

The log path is a development convenience and says so on every line it writes.
It is not a way to deliver mail to a real user: anyone who can read the server
log can take over an account, which is exactly why the link is never returned in
the HTTP response instead.
"""

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

DEFAULT_SMTP_PORT = 587


def is_configured() -> bool:
    """True when real mail can actually be sent."""
    return all(os.getenv(name) for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"))


def _sender() -> str:
    return os.getenv("SMTP_FROM") or os.getenv("SMTP_USERNAME") or "no-reply@leximind.local"


def send(to: str, subject: str, body: str) -> bool:
    """
    Deliver one plain-text message. Never raises.

    Returns True when it went out over SMTP. A False result is not an error the
    caller should surface — the endpoints above answer identically either way,
    so a mail outage can't be used to probe which accounts exist.
    """
    if not is_configured():
        logger.warning(
            "[dev] no SMTP configured — email for %s not sent. Subject: %s\n%s",
            to, subject, body,
        )
        return False

    message = EmailMessage()
    message["From"] = _sender()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", DEFAULT_SMTP_PORT))

    try:
        with smtplib.SMTP(host, port, timeout=15) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(os.getenv("SMTP_USERNAME"), os.getenv("SMTP_PASSWORD"))
            smtp.send_message(message)
        logger.info("Sent %r to %s", subject, to)
        return True
    except Exception as exc:  # noqa: BLE001 — bad credentials, DNS, TLS, offline
        # Logged without the body, which holds the reset link.
        logger.warning("SMTP delivery to %s failed: %s", to, exc)
        return False
