from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update

from app.site_models import DiscordBotHeartbeat, DiscordChannelRoute, DiscordOutboundEvent

ALLOWED_EVENT_KEYS = {
    "story_published",
    "trade_request",
    "announcement_posted",
    "control_center_restore",
    "standings_posted",
    "statistical_leaders_posted",
    "power_rankings_posted",
    "prospect_rankings_posted",
    "positional_rankings_posted",
    "calder_trophy_posted",
}

DEFAULT_EVENT_CHANNEL_KEY = {
    "story_published": "league-news",
    "trade_request": "transactions",
    "announcement_posted": "league-announcements",
    "control_center_restore": "staff-ops-alerts",
    "standings_posted": "standings",
    "statistical_leaders_posted": "goals-assists-points",
    "power_rankings_posted": "power-rankings",
    "prospect_rankings_posted": "prospect-rankings",
    "positional_rankings_posted": "positional-rankings",
    "calder_trophy_posted": "calder-trophy",
}

# Bot command keys for statistical leaderboards (BOWL Fantasy-style names; bots map to Discord channel names).
STAT_LEADER_BOT_COMMAND_KEYS = (
    "shots",
    "gap",
    "richard",
    "norris",
    "bourque",
    "langway",
    "selke",
    "ladybyng",
    "artross",
    "conn",
    "pminus",
    "green",
    "bs",
    "hits",
    "fights",
    "pim",
    "ppg",
    "shg",
    "gwg",
    "gva",
    "tka",
    "ovr",
    "grd",
    "gro",
    "vezina",
    "goaliew",
    "gl",
    "gaa",
    "saves",
    "svp",
    "so",
)

MAX_DELIVERY_ATTEMPTS = 3


def _event_idempotency_key(*, league_slug: str, event_key: str, channel_key: str, payload: dict) -> str:
    material = json.dumps(
        {
            "league_slug": str(league_slug or ""),
            "event_key": str(event_key or ""),
            "channel_key": str(channel_key or ""),
            "payload": payload or {},
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:64]


def _route_map(session, league_slug: str) -> dict[str, DiscordChannelRoute]:
    rows = session.scalars(
        select(DiscordChannelRoute).where(DiscordChannelRoute.league_slug == league_slug)
    ).all()
    return {str(r.event_key): r for r in rows}


def _migrate_ops_request_to_trade_request(session) -> None:
    """Rename legacy ops_request_status routes/events to trade_request (per-league, no duplicate key)."""
    legacy_routes = session.scalars(
        select(DiscordChannelRoute).where(DiscordChannelRoute.event_key == "ops_request_status")
    ).all()
    for row in legacy_routes:
        slug = str(row.league_slug or "")
        trade = session.scalar(
            select(DiscordChannelRoute).where(
                DiscordChannelRoute.league_slug == slug,
                DiscordChannelRoute.event_key == "trade_request",
            )
        )
        if trade is not None:
            session.delete(row)
        else:
            row.event_key = "trade_request"
    ev_upd = session.execute(
        update(DiscordOutboundEvent)
        .where(DiscordOutboundEvent.event_key == "ops_request_status")
        .values(event_key="trade_request")
    )
    if legacy_routes or (getattr(ev_upd, "rowcount", 0) or 0) > 0:
        session.commit()


def ensure_discord_routes(session, league_slug: str, updated_by_user_id: int | None = None) -> None:
    _migrate_ops_request_to_trade_request(session)
    by_key = _route_map(session, league_slug)
    now = datetime.utcnow()
    changed = False
    for key in sorted(ALLOWED_EVENT_KEYS):
        if key in by_key:
            continue
        session.add(
            DiscordChannelRoute(
                league_slug=league_slug,
                event_key=key,
                channel_key=DEFAULT_EVENT_CHANNEL_KEY.get(key, ""),
                is_enabled=True,
                updated_by_user_id=updated_by_user_id,
                updated_at=now,
            )
        )
        changed = True
    if changed:
        session.commit()


def list_discord_routes(session, league_slug: str) -> list[DiscordChannelRoute]:
    ensure_discord_routes(session, league_slug)
    return session.scalars(
        select(DiscordChannelRoute)
        .where(DiscordChannelRoute.league_slug == league_slug)
        .order_by(DiscordChannelRoute.event_key.asc(), DiscordChannelRoute.id.asc())
    ).all()


def update_discord_routes(session, league_slug: str, rows: list[dict], updated_by_user_id: int) -> list[dict]:
    ensure_discord_routes(session, league_slug, updated_by_user_id=updated_by_user_id)
    existing = _route_map(session, league_slug)
    now = datetime.utcnow()
    for item in rows:
        key = str(item.get("event_key") or "").strip()
        if key not in ALLOWED_EVENT_KEYS:
            continue
        row = existing.get(key)
        if row is None:
            continue
        row.channel_key = str(item.get("channel_key") or "").strip()[:64]
        row.is_enabled = bool(item.get("is_enabled"))
        row.updated_by_user_id = int(updated_by_user_id)
        row.updated_at = now
    session.commit()
    return [
        {"event_key": r.event_key, "channel_key": r.channel_key, "is_enabled": bool(r.is_enabled)}
        for r in list_discord_routes(session, league_slug)
    ]


def enqueue_discord_event(
    session,
    *,
    league_slug: str,
    event_key: str,
    payload: dict,
    created_by_user_id: int | None,
) -> DiscordOutboundEvent | None:
    key = str(event_key or "").strip()
    if key not in ALLOWED_EVENT_KEYS:
        return None
    ensure_discord_routes(session, league_slug)
    route = _route_map(session, league_slug).get(key)
    if route is None or not bool(route.is_enabled):
        return None
    payload_clean = payload or {}
    channel_key = str(route.channel_key or DEFAULT_EVENT_CHANNEL_KEY.get(key, ""))
    idem_key = _event_idempotency_key(
        league_slug=league_slug,
        event_key=key,
        channel_key=channel_key,
        payload=payload_clean,
    )
    existing = session.scalar(
        select(DiscordOutboundEvent)
        .where(
            DiscordOutboundEvent.league_slug == league_slug,
            DiscordOutboundEvent.idempotency_key == idem_key,
            DiscordOutboundEvent.status.in_(("pending", "sent", "failed")),
        )
        .order_by(DiscordOutboundEvent.id.desc())
        .limit(1)
    )
    if existing is not None:
        return existing
    row = DiscordOutboundEvent(
        league_slug=league_slug,
        event_key=key,
        channel_key=channel_key,
        idempotency_key=idem_key,
        payload_json=json.dumps(payload_clean),
        status="pending",
        attempts=0,
        last_error="",
        created_by_user_id=created_by_user_id,
        created_at=datetime.utcnow(),
        next_attempt_at=None,
        sent_at=None,
    )
    session.add(row)
    session.flush()
    return row


def list_outbound_events(session, *, league_slug: str, status: str = "", limit: int = 250) -> list[DiscordOutboundEvent]:
    q = select(DiscordOutboundEvent).where(DiscordOutboundEvent.league_slug == league_slug)
    st = str(status or "").strip().lower()
    if st in {"pending", "sent", "failed", "cancelled"}:
        q = q.where(DiscordOutboundEvent.status == st)
    return session.scalars(
        q.order_by(DiscordOutboundEvent.created_at.desc(), DiscordOutboundEvent.id.desc()).limit(max(1, int(limit)))
    ).all()


def fetch_pending_events_for_bot(session, *, league_slug: str, limit: int = 20) -> list[DiscordOutboundEvent]:
    now = datetime.utcnow()
    return session.scalars(
        select(DiscordOutboundEvent)
        .where(
            DiscordOutboundEvent.league_slug == league_slug,
            DiscordOutboundEvent.status == "pending",
            or_(DiscordOutboundEvent.next_attempt_at.is_(None), DiscordOutboundEvent.next_attempt_at <= now),
        )
        .order_by(DiscordOutboundEvent.created_at.asc(), DiscordOutboundEvent.id.asc())
        .limit(max(1, min(100, int(limit))))
    ).all()


def mark_event_sent(session, event_id: int) -> bool:
    row = session.get(DiscordOutboundEvent, int(event_id))
    if row is None or str(row.status) in {"cancelled", "sent"}:
        return False
    row.status = "sent"
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = ""
    row.next_attempt_at = None
    row.sent_at = datetime.utcnow()
    session.commit()
    return True


def mark_event_failed(session, event_id: int, error: str) -> bool:
    row = session.get(DiscordOutboundEvent, int(event_id))
    if row is None or str(row.status) == "cancelled":
        return False
    row.attempts = int(row.attempts or 0) + 1
    row.last_error = str(error or "").strip()[:1200]
    if int(row.attempts) >= MAX_DELIVERY_ATTEMPTS:
        row.status = "failed"
        row.next_attempt_at = None
    else:
        # Exponential-ish backoff: 1m, 3m, then final failure.
        delay_minutes = max(1, min(15, (2 ** max(0, int(row.attempts) - 1)) + (int(row.attempts) - 1)))
        row.status = "pending"
        row.next_attempt_at = datetime.utcnow() + timedelta(minutes=delay_minutes)
    session.commit()
    return True


def upsert_bot_heartbeat(
    session,
    *,
    league_slug: str,
    bot_name: str,
    bot_version: str,
    guild_id: str,
    extra: dict | None = None,
) -> DiscordBotHeartbeat:
    row = session.scalar(
        select(DiscordBotHeartbeat)
        .where(
            DiscordBotHeartbeat.league_slug == league_slug,
            DiscordBotHeartbeat.bot_name == str(bot_name or ""),
        )
        .limit(1)
    )
    if row is None:
        row = DiscordBotHeartbeat(
            league_slug=league_slug,
            bot_name=str(bot_name or "")[:120],
            bot_version=str(bot_version or "")[:64],
            guild_id=str(guild_id or "")[:64],
            last_seen_at=datetime.utcnow(),
            extra_json=json.dumps(extra or {}),
        )
        session.add(row)
    else:
        row.bot_version = str(bot_version or "")[:64]
        row.guild_id = str(guild_id or "")[:64]
        row.last_seen_at = datetime.utcnow()
        row.extra_json = json.dumps(extra or {})
    session.commit()
    return row


def list_heartbeats(session, *, league_slug: str, limit: int = 10) -> list[DiscordBotHeartbeat]:
    return session.scalars(
        select(DiscordBotHeartbeat)
        .where(DiscordBotHeartbeat.league_slug == league_slug)
        .order_by(DiscordBotHeartbeat.last_seen_at.desc(), DiscordBotHeartbeat.id.desc())
        .limit(max(1, int(limit)))
    ).all()
