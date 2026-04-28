from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_, select

from app.site_models import SiteAnnouncement

ALLOWED_ANNOUNCEMENT_LEVELS = {"info", "warn", "urgent"}


def active_announcement(session, league_slug: str, now: datetime | None = None) -> SiteAnnouncement | None:
    ts = now or datetime.utcnow()
    return session.scalar(
        select(SiteAnnouncement)
        .where(
            SiteAnnouncement.league_slug == league_slug,
            SiteAnnouncement.is_active.is_(True),
            or_(SiteAnnouncement.starts_at.is_(None), SiteAnnouncement.starts_at <= ts),
            or_(SiteAnnouncement.ends_at.is_(None), SiteAnnouncement.ends_at >= ts),
        )
        .order_by(SiteAnnouncement.created_at.desc(), SiteAnnouncement.id.desc())
        .limit(1)
    )

