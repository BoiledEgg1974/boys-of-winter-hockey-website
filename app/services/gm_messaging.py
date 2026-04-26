"""GM-to-GM in-league messaging (site DB, active memberships only)."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select, update

from app.league_db import db
from app.site_models import GmLeagueMessage, GmLeagueMembership, User


def gm_display_name(user: User | None) -> str:
    if not user:
        return "—"
    for attr in ("discord_name", "username"):
        v = (getattr(user, attr, None) or "").strip()
        if v:
            return v
    return (user.email or "").strip() or "—"


def active_peer_membership(league_slug: str, peer_user_id: int) -> GmLeagueMembership | None:
    return db.session.scalar(
        select(GmLeagueMembership)
        .where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.user_id == peer_user_id,
            GmLeagueMembership.status == "active",
        )
        .limit(1)
    )


def unread_count_for_user(league_slug: str, user_id: int) -> int:
    n = db.session.scalar(
        select(func.count())
        .select_from(GmLeagueMessage)
        .where(
            GmLeagueMessage.league_slug == league_slug,
            GmLeagueMessage.to_user_id == user_id,
            GmLeagueMessage.read_at.is_(None),
        )
    )
    return int(n or 0)


def inbox_threads(league_slug: str, user_id: int) -> list[dict[str, Any]]:
    """One row per other GM: last message, unread count, peer user id."""
    unread_msgs = db.session.scalars(
        select(GmLeagueMessage).where(
            GmLeagueMessage.league_slug == league_slug,
            GmLeagueMessage.to_user_id == user_id,
            GmLeagueMessage.read_at.is_(None),
        )
    ).all()
    unread_by_peer = Counter(m.from_user_id for m in unread_msgs)

    msgs = list(
        db.session.scalars(
            select(GmLeagueMessage)
            .where(
                GmLeagueMessage.league_slug == league_slug,
                or_(GmLeagueMessage.from_user_id == user_id, GmLeagueMessage.to_user_id == user_id),
            )
            .order_by(GmLeagueMessage.created_at.desc())
            .limit(400)
        ).all()
    )
    threads: dict[int, dict[str, Any]] = {}
    for m in msgs:
        peer = m.to_user_id if m.from_user_id == user_id else m.from_user_id
        if peer not in threads:
            threads[peer] = {
                "peer_id": peer,
                "last": m,
                "unread": int(unread_by_peer.get(peer, 0)),
            }
    out = list(threads.values())
    out.sort(key=lambda r: r["last"].created_at, reverse=True)
    return out


def thread_messages(league_slug: str, user_id: int, peer_id: int) -> list[GmLeagueMessage]:
    return list(
        db.session.scalars(
            select(GmLeagueMessage)
            .where(
                GmLeagueMessage.league_slug == league_slug,
                or_(
                    and_(GmLeagueMessage.from_user_id == user_id, GmLeagueMessage.to_user_id == peer_id),
                    and_(GmLeagueMessage.from_user_id == peer_id, GmLeagueMessage.to_user_id == user_id),
                ),
            )
            .order_by(GmLeagueMessage.created_at.asc(), GmLeagueMessage.id.asc())
        ).all()
    )


def mark_thread_read(league_slug: str, recipient_id: int, peer_id: int) -> None:
    db.session.execute(
        update(GmLeagueMessage)
        .where(
            GmLeagueMessage.league_slug == league_slug,
            GmLeagueMessage.from_user_id == peer_id,
            GmLeagueMessage.to_user_id == recipient_id,
            GmLeagueMessage.read_at.is_(None),
        )
        .values(read_at=datetime.utcnow())
    )


def list_other_active_gms(league_slug: str, exclude_user_id: int) -> list[tuple[GmLeagueMembership, User]]:
    rows = db.session.execute(
        select(GmLeagueMembership, User)
        .join(User, User.id == GmLeagueMembership.user_id)
        .where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
            GmLeagueMembership.user_id != exclude_user_id,
        )
        .order_by(User.discord_name, User.email)
    ).all()
    return [(r[0], r[1]) for r in rows]
