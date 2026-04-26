"""In-app GM notifications (site DB), e.g. news approve/deny — no email."""
from __future__ import annotations

from sqlalchemy import func, select

from app.league_db import db
from app.site_models import ApRedemptionRequest, GmInAppNotification, NewsArticle


def unread_notifications_count(league_slug: str, user_id: int) -> int:
    n = db.session.scalar(
        select(func.count())
        .select_from(GmInAppNotification)
        .where(
            GmInAppNotification.league_slug == league_slug,
            GmInAppNotification.user_id == user_id,
            GmInAppNotification.read_at.is_(None),
        )
    )
    return int(n or 0)


def gm_inbox_badge_unread(league_slug: str, user_id: int) -> int:
    from app.services.gm_messaging import unread_count_for_user

    return unread_count_for_user(league_slug, user_id) + unread_notifications_count(
        league_slug, user_id
    )


def list_notifications(league_slug: str, user_id: int, *, limit: int = 40) -> list[GmInAppNotification]:
    return list(
        db.session.scalars(
            select(GmInAppNotification)
            .where(
                GmInAppNotification.league_slug == league_slug,
                GmInAppNotification.user_id == user_id,
            )
            .order_by(GmInAppNotification.created_at.desc())
            .limit(limit)
        ).all()
    )


def notify_news_approved(league_slug: str, art: NewsArticle) -> None:
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=art.author_user_id,
            kind="news_approved",
            title=f"Approved: {art.title[:380]}",
            body="Your Around the League submission was approved and is live under Headlines / the home page.",
            article_id=art.id,
        )
    )
    db.session.commit()


def notify_news_denied(league_slug: str, art: NewsArticle) -> None:
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=art.author_user_id,
            kind="news_denied",
            title=f"Not approved: {art.title[:380]}",
            body="Your submission was not approved. You can submit a revised article from League News when ready.",
            article_id=None,
        )
    )
    db.session.commit()


def notify_redemption_approved(league_slug: str, req: ApRedemptionRequest) -> None:
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="redemption_approved",
            title=f"AP redemption approved (#{req.id})",
            body=f"Approved. {int(req.total_cost)} AP was deducted from your balance.",
            article_id=None,
        )
    )
    db.session.commit()


def notify_redemption_denied(league_slug: str, req: ApRedemptionRequest) -> None:
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="redemption_denied",
            title=f"AP redemption denied (#{req.id})",
            body="Denied. No AP was deducted; you can submit another request when ready.",
            article_id=None,
        )
    )
    db.session.commit()
