"""Private Discord DM queue for user-targeted site notifications."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from typing import Any

from flask import current_app, has_app_context, url_for
from sqlalchemy import or_, select

from app.services.discord_events import DISCORD_SNOWFLAKE_PATTERN, MAX_DELIVERY_ATTEMPTS
from app.site_models import DiscordDirectMessageEvent, User


SENSITIVE_EVENT_KEYS = frozenset(
    {
        "gm_direct_message",
        "trade_partner_review",
        "trade_commish_review",
        "trade_outcome_proposer",
        "trade_outcome_partner",
        "admin_review_membership",
    }
)


def _now() -> datetime:
    return datetime.utcnow()


def _hash_key(*parts: object) -> str:
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _league_public_url(league_slug: str, path: str = "/gm/messages") -> str:
    rel = path if path.startswith("/") else f"/{path}"
    if has_app_context():
        try:
            return url_for("site_portal.gm_messages_inbox", _external=True)
        except Exception:
            pass
    base = ""
    if has_app_context():
        base = str(current_app.config.get("SITE_PUBLIC_BASE_URL") or "").strip().rstrip("/")
    return f"{base}/{league_slug}{rel}" if base else f"/{league_slug}{rel}"


def _clean_preview(text: str, *, limit: int = 220) -> str:
    val = str(text or "").strip().replace("\r\n", "\n")
    val = " ".join(line.strip() for line in val.splitlines() if line.strip())
    if len(val) <= limit:
        return val
    return val[: limit - 1].rstrip() + "…"


def should_include_preview(event_key: str) -> bool:
    return event_key not in SENSITIVE_EVENT_KEYS


def find_discord_user_id_conflict(
    session,
    *,
    discord_user_id: str,
    exclude_user_id: int | None = None,
) -> User | None:
    """Return another active site user already using this Discord snowflake."""
    snowflake = str(discord_user_id or "").strip()
    if not snowflake:
        return None
    clauses = [
        User.discord_user_id == snowflake,
        User.revoked_at.is_(None),
    ]
    if exclude_user_id is not None:
        clauses.append(User.id != int(exclude_user_id))
    return session.scalar(select(User).where(*clauses).order_by(User.id.asc()).limit(1))


def enqueue_direct_message(
    session,
    *,
    league_slug: str,
    recipient_user_id: int,
    event_key: str,
    title: str,
    body: str = "",
    source_type: str = "",
    source_id: str | int = "",
    url: str = "",
    preview: str | None = None,
) -> DiscordDirectMessageEvent | None:
    """Queue a private Discord DM for a site user, if they have opted in."""
    user = session.get(User, int(recipient_user_id))
    if user is None:
        return None
    discord_user_id = str(getattr(user, "discord_user_id", None) or "").strip()
    if not discord_user_id or not DISCORD_SNOWFLAKE_PATTERN.match(discord_user_id):
        return None
    if getattr(user, "discord_dm_enabled", True) is False:
        return None
    # Shared snowflakes deliver every GM's alert to one Discord account (Detroit/Atlanta mix-ups).
    conflict = find_discord_user_id_conflict(
        session,
        discord_user_id=discord_user_id,
        exclude_user_id=int(recipient_user_id),
    )
    if conflict is not None:
        return None

    source_type_s = str(source_type or event_key or "site_notification").strip()[:64]
    source_id_s = str(source_id or "").strip()[:64]
    event_key_s = str(event_key or "site_notification").strip()[:64]
    idempotency_key = _hash_key(
        "dm",
        league_slug,
        int(recipient_user_id),
        event_key_s,
        source_type_s,
        source_id_s,
        str(title or "").strip(),
    )
    existing = session.scalar(
        select(DiscordDirectMessageEvent)
        .where(DiscordDirectMessageEvent.idempotency_key == idempotency_key)
        .limit(1)
    )
    if existing is not None:
        return existing

    include_preview = preview is not None or should_include_preview(event_key_s)
    preview_text = _clean_preview(preview if preview is not None else body) if include_preview else ""
    payload = {
        "discord_name": getattr(user, "discord_name", "") or "there",
        "title": str(title or "New site notification").strip()[:400],
        "body": _clean_preview(body, limit=900),
        "preview": preview_text,
        "url": url or _league_public_url(league_slug),
        "league_slug": league_slug,
        "event_key": event_key_s,
    }
    row = DiscordDirectMessageEvent(
        league_slug=str(league_slug or "").strip(),
        recipient_user_id=int(recipient_user_id),
        discord_user_id=discord_user_id,
        event_key=event_key_s,
        source_type=source_type_s,
        source_id=source_id_s,
        idempotency_key=idempotency_key,
        payload_json=json.dumps(payload, separators=(",", ":")),
        status="pending",
        attempts=0,
        created_at=_now(),
    )
    session.add(row)
    return row


def enqueue_notification_dm(
    session,
    *,
    league_slug: str,
    notification,
) -> DiscordDirectMessageEvent | None:
    return enqueue_direct_message(
        session,
        league_slug=league_slug,
        recipient_user_id=int(notification.user_id),
        event_key=str(notification.kind or "site_notification"),
        title=str(notification.title or "New site notification"),
        body=str(notification.body or ""),
        source_type="gm_notification",
        source_id=int(notification.id),
    )


def fetch_pending_direct_messages_for_bot(
    session, *, league_slug: str, limit: int = 20
) -> list[DiscordDirectMessageEvent]:
    now = _now()
    rows = session.scalars(
        select(DiscordDirectMessageEvent)
        .where(
            DiscordDirectMessageEvent.league_slug == league_slug,
            DiscordDirectMessageEvent.status == "pending",
            or_(
                DiscordDirectMessageEvent.next_attempt_at.is_(None),
                DiscordDirectMessageEvent.next_attempt_at <= now,
            ),
        )
        .order_by(DiscordDirectMessageEvent.created_at.asc(), DiscordDirectMessageEvent.id.asc())
        .limit(max(1, min(int(limit or 20), 100)))
    ).all()
    return list(rows)


def serialize_direct_messages_for_bot(rows: list[DiscordDirectMessageEvent]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        out.append(
            {
                "id": row.id,
                "league_slug": row.league_slug,
                "recipient_user_id": row.recipient_user_id,
                "discord_user_id": row.discord_user_id,
                "event_key": row.event_key,
                "source_type": row.source_type,
                "source_id": row.source_id,
                "payload": payload,
                "attempts": row.attempts,
            }
        )
    return out


def mark_direct_message_sent(
    session,
    event_id: int,
    *,
    discord_channel_id: str = "",
    discord_message_id: str = "",
) -> bool:
    from app.sqlite_retry import write_with_sqlite_retry

    def _mark() -> bool:
        row = session.get(DiscordDirectMessageEvent, int(event_id))
        if row is None:
            return False
        row.status = "sent"
        row.sent_at = _now()
        row.last_error = ""
        row.discord_channel_id = str(discord_channel_id or "")[:32]
        row.discord_message_id = str(discord_message_id or "")[:32]
        return True

    return bool(write_with_sqlite_retry(session, _mark))


def mark_direct_message_failed(session, event_id: int, error: str) -> bool:
    from app.sqlite_retry import write_with_sqlite_retry

    def _mark() -> bool:
        row = session.get(DiscordDirectMessageEvent, int(event_id))
        if row is None:
            return False
        row.attempts = int(row.attempts or 0) + 1
        row.last_error = str(error or "delivery failed")[:2000]
        if row.attempts >= MAX_DELIVERY_ATTEMPTS:
            row.status = "failed"
        else:
            row.status = "pending"
            row.next_attempt_at = _now() + timedelta(minutes=min(30, 2 ** row.attempts))
        return True

    return bool(write_with_sqlite_retry(session, _mark))
