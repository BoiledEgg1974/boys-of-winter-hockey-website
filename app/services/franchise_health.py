from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select

from app.models import Team
from app.site_models import GmApprovalRequest, GmLeagueMembership, GmLeagueMessage, NewsArticle, User


def _days_since(ts: datetime | None) -> int | None:
    if ts is None:
        return None
    return max(0, int((datetime.utcnow() - ts).total_seconds() // 86400))


def build_franchise_health_rows(session, league_slug: str) -> list[dict]:
    memberships = session.scalars(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    user_ids = {int(m.user_id) for m in memberships}
    team_ids = {int(m.team_id) for m in memberships}
    users = {int(u.id): u for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    teams = {int(t.id): t for t in session.scalars(select(Team).where(Team.id.in_(team_ids))).all()} if team_ids else {}

    rows: list[dict] = []
    for m in memberships:
        uid = int(m.user_id)
        tid = int(m.team_id)
        u = users.get(uid)
        tm = teams.get(tid)
        last_msg = session.scalar(
            select(GmLeagueMessage)
            .where(
                GmLeagueMessage.league_slug == league_slug,
                GmLeagueMessage.from_user_id == uid,
            )
            .order_by(GmLeagueMessage.created_at.desc())
            .limit(1)
        )
        last_news = session.scalar(
            select(NewsArticle)
            .where(
                NewsArticle.league_slug == league_slug,
                NewsArticle.author_user_id == uid,
            )
            .order_by(NewsArticle.created_at.desc())
            .limit(1)
        )
        pending_count = int(
            session.scalar(
                select(func.count(GmApprovalRequest.id))
                .where(
                    GmApprovalRequest.league_slug == league_slug,
                    GmApprovalRequest.team_id == tid,
                    GmApprovalRequest.status == "pending",
                )
            )
            or 0
        )
        latest_ts = None
        for ts in (m.approved_at, m.created_at, getattr(last_msg, "created_at", None), getattr(last_news, "created_at", None)):
            if ts is not None and (latest_ts is None or ts > latest_ts):
                latest_ts = ts
        inactivity_days = _days_since(latest_ts)
        if inactivity_days is None:
            health = "unknown"
        elif inactivity_days >= 21:
            health = "critical"
        elif inactivity_days >= 10:
            health = "watch"
        else:
            health = "healthy"
        rows.append(
            {
                "team": tm,
                "user": u,
                "last_activity_at": latest_ts,
                "inactivity_days": inactivity_days,
                "health": health,
                "pending_requests": pending_count,
            }
        )
    rows.sort(key=lambda r: (r.get("inactivity_days") or 10**6), reverse=True)
    return rows
