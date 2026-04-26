"""Shared SMTP send (Join League, AP redemptions, etc.)."""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from flask import current_app


def send_site_email(*, subject: str, body: str, to_addrs: list[str]) -> None:
    """Send plain-text email using current_app mail config. Raises if SMTP host unset."""
    recipient_list = [a.strip() for a in to_addrs if a and str(a).strip()]
    if not recipient_list:
        raise RuntimeError("No recipients for email.")

    smtp_host = str(current_app.config.get("MAIL_SMTP_HOST", "")).strip()
    smtp_port = int(current_app.config.get("MAIL_SMTP_PORT", 587))
    smtp_user = str(current_app.config.get("MAIL_SMTP_USERNAME", "")).strip()
    smtp_pass = str(current_app.config.get("MAIL_SMTP_PASSWORD", "")).strip()
    smtp_from = str(current_app.config.get("MAIL_FROM", smtp_user or recipient_list[0])).strip()
    use_tls = bool(current_app.config.get("MAIL_SMTP_USE_TLS", True))
    use_ssl = bool(current_app.config.get("MAIL_SMTP_USE_SSL", False))

    if not smtp_host:
        raise RuntimeError("MAIL_SMTP_HOST is not configured.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = ", ".join(recipient_list)
    msg.set_content(body)

    if use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            if smtp_user:
                server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        if use_tls:
            server.starttls()
        if smtp_user:
            server.login(smtp_user, smtp_pass)
        server.send_message(msg)
