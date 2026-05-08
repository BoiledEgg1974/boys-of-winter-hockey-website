"""Password reset token creation, validation, and email delivery."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from flask import current_app, url_for
from sqlalchemy import delete, select
from werkzeug.security import generate_password_hash

from app.league_db import db
from app.mail_util import send_site_email
from app.site_models import PasswordResetToken, User


def _token_sha256(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _ttl_minutes() -> int:
    raw = current_app.config.get("PASSWORD_RESET_TOKEN_TTL_MINUTES", 60)
    try:
        return max(10, int(raw))
    except (TypeError, ValueError):
        return 60


def issue_password_reset_token(user: User) -> str:
    """Create a single-use reset token and return the plain token."""
    now = datetime.utcnow()
    db.session.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.user_id == int(user.id),
            PasswordResetToken.used_at.is_(None),
        )
    )
    raw_token = secrets.token_urlsafe(32)
    token = PasswordResetToken(
        user_id=int(user.id),
        token_hash=_token_sha256(raw_token),
        created_at=now,
        expires_at=now + timedelta(minutes=_ttl_minutes()),
    )
    db.session.add(token)
    db.session.commit()
    return raw_token


def send_password_reset_email(*, user: User, raw_token: str) -> None:
    reset_url = url_for("hub_auth.reset_password", token=raw_token, _external=True)
    subject = "[Boys of Winter] Reset your GM account password"
    body = (
        f"Hi,\n\n"
        f"We received a request to reset the password for your GM account ({user.email}).\n\n"
        f"Use this link to set a new password:\n{reset_url}\n\n"
        f"If you did not request this, you can ignore this message.\n"
        f"This link expires in {_ttl_minutes()} minutes.\n"
    )
    send_site_email(subject=subject, body=body, to_addrs=[str(user.email)])


def find_user_for_reset_token(raw_token: str) -> User | None:
    if not raw_token:
        return None
    digest = _token_sha256(raw_token)
    now = datetime.utcnow()
    row = db.session.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == digest,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at >= now,
        )
        .limit(1)
    )
    if row is None:
        return None
    user = db.session.get(User, int(row.user_id))
    if user is None or user.revoked_at is not None:
        return None
    return user


def consume_reset_token_and_update_password(*, raw_token: str, new_password: str) -> bool:
    """Atomically consume a valid token and set a new password."""
    if not raw_token or not new_password:
        return False
    digest = _token_sha256(raw_token)
    now = datetime.utcnow()
    row = db.session.scalar(
        select(PasswordResetToken)
        .where(
            PasswordResetToken.token_hash == digest,
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at >= now,
        )
        .limit(1)
    )
    if row is None:
        return False
    user = db.session.get(User, int(row.user_id))
    if user is None or user.revoked_at is not None:
        return False
    user.password_hash = generate_password_hash(new_password)
    row.used_at = now
    # Invalidate other unexpired, unused tokens for this user.
    db.session.execute(
        delete(PasswordResetToken).where(
            PasswordResetToken.user_id == int(user.id),
            PasswordResetToken.id != int(row.id),
            PasswordResetToken.used_at.is_(None),
        )
    )
    db.session.commit()
    return True
