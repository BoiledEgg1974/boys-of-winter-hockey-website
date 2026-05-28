"""Shared SMTP send (Join League, AP redemptions, etc.)."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from flask import current_app

_log = logging.getLogger(__name__)


def _env_mail_value(key: str, default: str = "") -> str:
    raw = str(current_app.config.get(key, default) or "").strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1].strip()
    return raw


def _smtp_password() -> str:
    """Gmail App Passwords are 16 chars; Google displays them in groups but SMTP wants no spaces."""
    return _env_mail_value("MAIL_SMTP_PASSWORD").replace(" ", "")


def send_site_email(*, subject: str, body: str, to_addrs: list[str]) -> None:
    """Send plain-text email using current_app mail config. Raises if SMTP host unset."""
    recipient_list = [a.strip() for a in to_addrs if a and str(a).strip()]
    if not recipient_list:
        raise RuntimeError("No recipients for email.")

    smtp_host = str(current_app.config.get("MAIL_SMTP_HOST", "")).strip()
    smtp_port = int(current_app.config.get("MAIL_SMTP_PORT", 587))
    smtp_user = _env_mail_value("MAIL_SMTP_USERNAME")
    smtp_pass = _smtp_password()
    smtp_from = _env_mail_value("MAIL_FROM", smtp_user or recipient_list[0])
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


def user_facing_email_send_error(exc: BaseException) -> str:
    """Plain-language message for applicants/users; details stay in server logs."""
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return (
            "We could not send your application right now because the league mail server "
            "rejected its login. Please try again later or contact the commissioner directly."
        )
    msg = str(exc or "").strip()
    if "MAIL_SMTP_HOST is not configured" in msg:
        return (
            "We could not send your application right now (mail is not configured on the server). "
            "Please contact the league commissioner directly."
        )
    if isinstance(exc, (smtplib.SMTPException, TimeoutError, OSError)):
        return (
            "We could not send your application right now due to a mail delivery problem. "
            "Please try again later or contact the league commissioner directly."
        )
    return (
        "We could not send your application right now. "
        "Please try again later or contact the league commissioner directly."
    )


def log_email_send_failure(context: str, exc: BaseException) -> None:
    _log.exception("%s failed: %s", context, exc)


def smtp_config_diagnostics() -> dict[str, object]:
    """Non-secret SMTP config check for admin pages (requires app context)."""
    user = _env_mail_value("MAIL_SMTP_USERNAME")
    pwd = _smtp_password()
    host = str(current_app.config.get("MAIL_SMTP_HOST", "") or "").strip()
    port = int(current_app.config.get("MAIL_SMTP_PORT", 587) or 587)
    use_tls = bool(current_app.config.get("MAIL_SMTP_USE_TLS", True))
    use_ssl = bool(current_app.config.get("MAIL_SMTP_USE_SSL", False))
    return {
        "host": host,
        "port": port,
        "username": user,
        "from_addr": _env_mail_value("MAIL_FROM", user),
        "password_length": len(pwd),
        "password_length_ok": len(pwd) == 16,
        "has_password": bool(pwd),
        "use_tls": use_tls,
        "use_ssl": use_ssl,
        "tls_mode": "SSL" if use_ssl else ("TLS" if use_tls else "None"),
        "recipient": str(current_app.config.get("JOIN_LEAGUE_RECIPIENT", "") or "").strip(),
    }


def test_smtp_login() -> dict[str, object]:
    """Connect and authenticate only; does not send mail."""
    diag = smtp_config_diagnostics()
    host = str(diag.get("host") or "")
    user = str(diag.get("username") or "")
    pwd = _smtp_password()
    port = int(diag.get("port") or 587)
    use_tls = bool(diag.get("use_tls"))
    use_ssl = bool(diag.get("use_ssl"))

    if not host:
        return {**diag, "ok": False, "error": "MAIL_SMTP_HOST is not configured."}
    if not user:
        return {**diag, "ok": False, "error": "MAIL_SMTP_USERNAME is not configured."}
    if not pwd:
        return {**diag, "ok": False, "error": "MAIL_SMTP_PASSWORD is not configured."}
    if len(pwd) != 16:
        return {
            **diag,
            "ok": False,
            "error": f"App password length is {len(pwd)}; Gmail App Passwords are exactly 16 characters.",
        }

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=30) as server:
                server.login(user, pwd)
        else:
            with smtplib.SMTP(host, port, timeout=30) as server:
                if use_tls:
                    server.starttls()
                server.login(user, pwd)
    except smtplib.SMTPAuthenticationError as exc:
        return {
            **diag,
            "ok": False,
            "error": str(exc),
            "hint": (
                "Gmail rejected the login. Generate a new App Password for bowlfhmhockey@gmail.com "
                "(Google Account → Security → App passwords), paste the 16 characters into MAIL_SMTP_PASSWORD, "
                "reload the web app, then visit https://accounts.google.com/DisplayUnlockCaptcha while signed "
                "into that account if Google blocked sign-in from the server."
            ),
        }
    except Exception as exc:
        return {**diag, "ok": False, "error": str(exc)}

    return {**diag, "ok": True, "error": ""}
