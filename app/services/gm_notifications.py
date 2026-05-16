"""In-app GM notifications (site DB), e.g. news approve/deny — no email."""
from __future__ import annotations

from sqlalchemy import func, select

from app.league_db import db
from app.services.staff_catalog import staff_role_label
from app.site_models import ApRedemptionRequest, GmInAppNotification, GmLeagueMembership, NewsArticle, StaffChangeRequest


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


def notify_all_gms_admin_article(league_slug: str, art: NewsArticle) -> None:
    """In-app notification to every active GM (league office broadcast)."""
    user_ids = db.session.scalars(
        select(GmLeagueMembership.user_id).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    seen: set[int] = set()
    body = (art.body or "").strip().replace("\r\n", "\n")
    if len(body) > 900:
        body = body[:900] + "…"
    for uid in user_ids:
        if uid in seen:
            continue
        seen.add(int(uid))
        db.session.add(
            GmInAppNotification(
                league_slug=league_slug,
                user_id=int(uid),
                kind="admin_league_article",
                title=f"League office: {art.title[:380]}",
                body=body or "New league article — open to read the full story.",
                article_id=art.id,
            )
        )
    db.session.commit()


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


def notify_trade_proposal_partner(
    league_slug: str, *, partner_user_id: int, proposal_id: int, summary_preview: str
) -> None:
    """Partner GM: review / approve in Trade Tool flow. ``article_id`` stores proposal id."""
    body = (summary_preview or "").strip().replace("\r\n", "\n")
    if len(body) > 900:
        body = body[:900] + "…"
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(partner_user_id),
            kind="trade_partner_review",
            title="Trade proposal — your approval needed",
            body=body or "Open the trade proposal to approve or decline.",
            article_id=int(proposal_id),
        )
    )


def notify_trade_proposal_commissioners(
    league_slug: str, *, commissioner_user_ids: list[int], proposal_id: int, summary_preview: str
) -> None:
    body = (summary_preview or "").strip().replace("\r\n", "\n")
    if len(body) > 900:
        body = body[:900] + "…"
    for uid in commissioner_user_ids:
        db.session.add(
            GmInAppNotification(
                league_slug=league_slug,
                user_id=int(uid),
                kind="trade_commish_review",
                title="Trade proposal — commissioner review",
                body=body or "Both GMs approved; open for final approval or denial.",
                article_id=int(proposal_id),
            )
        )


def notify_trade_outcome_proposer(
    league_slug: str, *, proposer_user_id: int, proposal_id: int, title: str, body: str
) -> None:
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(proposer_user_id),
            kind="trade_outcome_proposer",
            title=title[:400],
            body=body[:4000],
            article_id=int(proposal_id),
        )
    )


def notify_trade_outcome_partner(
    league_slug: str, *, partner_user_id: int, proposal_id: int, title: str, body: str
) -> None:
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(partner_user_id),
            kind="trade_outcome_partner",
            title=title[:400],
            body=body[:4000],
            article_id=int(proposal_id),
        )
    )


def _staff_req_ts(req: StaffChangeRequest) -> str:
    ts = req.created_at
    if ts is None:
        return ""
    return ts.strftime("%Y-%m-%d %H:%M UTC")


def notify_staff_hire_approved(league_slug: str, req: StaffChangeRequest) -> None:
    role = staff_role_label(req.role)
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="staff_hire_approved",
            title=f"Staff hire approved (#{req.id})",
            body=f"{req.staff_name} is now your {role}. Requested {_staff_req_ts(req)}.",
            article_id=req.id,
        )
    )
    db.session.commit()


def notify_staff_fire_approved(league_slug: str, req: StaffChangeRequest) -> None:
    role = staff_role_label(req.role)
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="staff_fire_approved",
            title=f"Staff release approved (#{req.id})",
            body=f"{req.staff_name} ({role}) has been released. Requested {_staff_req_ts(req)}.",
            article_id=req.id,
        )
    )
    db.session.commit()


def notify_staff_change_denied(league_slug: str, req: StaffChangeRequest) -> None:
    action = "hire" if req.request_type == "hire" else "release"
    note = (req.admin_note or "").strip()
    body = f"Your staff {action} request for {req.staff_name} was denied. Requested {_staff_req_ts(req)}."
    if note:
        body += f" Note: {note}"
    db.session.add(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="staff_change_denied",
            title=f"Staff request denied (#{req.id})",
            body=body[:4000],
            article_id=req.id,
        )
    )
    db.session.commit()
