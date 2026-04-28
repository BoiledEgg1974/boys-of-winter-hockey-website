from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from app.site_models import GmApprovalRequest, MemberWatchlistItem, NewsArticle, User


def build_member_watchlist_digest(session, *, league_slug: str) -> dict:
    items = session.scalars(
        select(MemberWatchlistItem)
        .where(MemberWatchlistItem.league_slug == league_slug)
        .order_by(MemberWatchlistItem.created_at.desc(), MemberWatchlistItem.id.desc())
        .limit(1000)
    ).all()
    user_ids = {int(i.user_id) for i in items}
    users_by_id = (
        {int(u.id): u for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )
    by_user: dict[int, list[MemberWatchlistItem]] = defaultdict(list)
    for i in items:
        by_user[int(i.user_id)].append(i)

    latest_news = session.scalars(
        select(NewsArticle)
        .where(NewsArticle.league_slug == league_slug)
        .order_by(NewsArticle.created_at.desc(), NewsArticle.id.desc())
        .limit(50)
    ).all()
    pending_ops = session.scalars(
        select(GmApprovalRequest)
        .where(GmApprovalRequest.league_slug == league_slug, GmApprovalRequest.status == "pending")
        .order_by(GmApprovalRequest.created_at.desc(), GmApprovalRequest.id.desc())
        .limit(50)
    ).all()

    members = []
    for uid, watch_items in by_user.items():
        u = users_by_id.get(uid)
        counts: dict[str, int] = defaultdict(int)
        for wi in watch_items:
            counts[str(wi.target_type or "unknown")] += 1
        members.append(
            {
                "user": u,
                "watch_count": len(watch_items),
                "type_counts": dict(counts),
                "items": watch_items[:20],
            }
        )
    members.sort(key=lambda r: (-int(r["watch_count"]), int(getattr(r["user"], "id", 0) or 0)))

    return {
        "members": members,
        "watchlist_total": len(items),
        "recent_news": latest_news,
        "pending_ops": pending_ops,
        "generated_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
        "note": "Digest capped at 1000 watchlist rows; news/ops lists capped at 50 for performance.",
    }
