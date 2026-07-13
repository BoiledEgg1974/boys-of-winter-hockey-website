"""In-app GM notifications (site DB), e.g. news approve/deny — no email."""
from __future__ import annotations

from sqlalchemy import func, select

from app.league_db import db
from app.sqlite_retry import commit_with_sqlite_retry
from app.services.staff_catalog import staff_role_label
from app.site_models import ApRedemptionRequest, GmInAppNotification, GmLeagueMembership, NewsArticle, RfaOfferRequest, StaffChangeRequest


def _commit_notifications() -> None:
    commit_with_sqlite_retry(db.session)


def _add_notification(notification: GmInAppNotification) -> GmInAppNotification:
    db.session.add(notification)
    db.session.flush()
    try:
        from app.services.discord_direct_messages import enqueue_notification_dm

        enqueue_notification_dm(
            db.session,
            league_slug=notification.league_slug,
            notification=notification,
        )
    except Exception:
        # Discord DMs are advisory and should never block the site message itself.
        pass
    return notification


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
        _add_notification(
            GmInAppNotification(
                league_slug=league_slug,
                user_id=int(uid),
                kind="admin_league_article",
                title=f"League office: {art.title[:380]}",
                body=body or "New league article — open to read the full story.",
                article_id=art.id,
            )
        )
    _commit_notifications()


def notify_news_approved(league_slug: str, art: NewsArticle) -> None:
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=art.author_user_id,
            kind="news_approved",
            title=f"Approved: {art.title[:380]}",
            body="Your Around the League submission was approved and is live under Headlines / the home page.",
            article_id=art.id,
        )
    )
    _commit_notifications()


def notify_news_denied(league_slug: str, art: NewsArticle) -> None:
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=art.author_user_id,
            kind="news_denied",
            title=f"Not approved: {art.title[:380]}",
            body="Your submission was not approved. You can submit a revised article from League News when ready.",
            article_id=None,
        )
    )
    _commit_notifications()


def notify_redemption_approved(league_slug: str, req: ApRedemptionRequest) -> None:
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="redemption_approved",
            title=f"AP redemption approved (#{req.id})",
            body=f"Approved. {int(req.total_cost)} AP was deducted from your balance.",
            article_id=None,
        )
    )
    _commit_notifications()


def notify_redemption_denied(league_slug: str, req: ApRedemptionRequest) -> None:
    note = (req.admin_note or "").strip()
    body = "Denied. No AP was deducted; you can submit another request when ready."
    if note:
        body += f" Reason: {note}"
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="redemption_denied",
            title=f"AP redemption denied (#{req.id})",
            body=body[:4000],
            article_id=None,
        )
    )
    _commit_notifications()


def notify_trade_proposal_partner(
    league_slug: str, *, partner_user_id: int, proposal_id: int, summary_preview: str
) -> None:
    """Partner GM: review / approve in Trade Tool flow. ``article_id`` stores proposal id."""
    body = (summary_preview or "").strip().replace("\r\n", "\n")
    if len(body) > 900:
        body = body[:900] + "…"
    _add_notification(
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
        _add_notification(
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
    _add_notification(
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
    _add_notification(
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
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="staff_hire_approved",
            title=f"Staff hire approved (#{req.id})",
            body=f"{req.staff_name} is now your {role}. Requested {_staff_req_ts(req)}.",
            article_id=req.id,
        )
    )
    _commit_notifications()


def notify_staff_fire_approved(league_slug: str, req: StaffChangeRequest) -> None:
    role = staff_role_label(req.role)
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="staff_fire_approved",
            title=f"Staff release approved (#{req.id})",
            body=f"{req.staff_name} ({role}) has been released. Requested {_staff_req_ts(req)}.",
            article_id=req.id,
        )
    )
    _commit_notifications()


def notify_staff_change_denied(league_slug: str, req: StaffChangeRequest) -> None:
    action = "hire" if req.request_type == "hire" else "release"
    note = (req.admin_note or "").strip()
    body = f"Your staff {action} request for {req.staff_name} was denied. Requested {_staff_req_ts(req)}."
    if note:
        body += f" Note: {note}"
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=req.user_id,
            kind="staff_change_denied",
            title=f"Staff request denied (#{req.id})",
            body=body[:4000],
            article_id=req.id,
        )
    )
    _commit_notifications()


def _rfa_player_name(player) -> str:
    if player is None:
        return "Player"
    return str(getattr(player, "full_name", None) or f"Player #{getattr(player, 'id', '?')}")


def _rfa_offer_summary(req: RfaOfferRequest, *, player=None) -> str:
    name = _rfa_player_name(player)
    return (
        f"{name}: ${int(req.offer_salary):,} × {int(req.offer_years)} yr"
        f" · request #{int(req.id)}"
    )


def notify_rfa_player_rejected(league_slug: str, req: RfaOfferRequest, *, player=None) -> None:
    body = f"The player rejected your offer sheet. {_rfa_offer_summary(req, player=player)}"
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(req.offering_user_id),
            kind="rfa_player_rejected",
            title=f"RFA offer rejected (#{req.id})",
            body=body[:4000],
            article_id=int(req.id),
        )
    )
    _commit_notifications()


def notify_rfa_awaiting_equalization(league_slug: str, req: RfaOfferRequest, *, player=None) -> None:
    summary = _rfa_offer_summary(req, player=player)
    body = (
        f"Player accepted your offer. Submit an equalization trade agreement within 24 hours.\n{summary}"
    )
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(req.offering_user_id),
            kind="rfa_awaiting_equalization",
            title=f"RFA accepted — equalization needed (#{req.id})",
            body=body[:4000],
            article_id=int(req.id),
        )
    )
    rights_mem = db.session.scalar(
        select(GmLeagueMembership.user_id).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.team_id == int(req.rights_team_id),
            GmLeagueMembership.status == "active",
        ).limit(1)
    )
    if rights_mem:
        _add_notification(
            GmInAppNotification(
                league_slug=league_slug,
                user_id=int(rights_mem),
                kind="rfa_awaiting_equalization",
                title=f"RFA equalization trade required (#{req.id})",
                body=(
                    f"Your former RFA accepted an offer sheet. Work with the offering GM on equalization.\n{summary}"
                )[:4000],
                article_id=int(req.id),
            )
        )
    _commit_notifications()


def notify_rfa_awaiting_match(league_slug: str, req: RfaOfferRequest, *, player=None) -> None:
    rights_mem = db.session.scalar(
        select(GmLeagueMembership.user_id).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.team_id == int(req.rights_team_id),
            GmLeagueMembership.status == "active",
        ).limit(1)
    )
    if not rights_mem:
        _commit_notifications()
        return
    comp_note = ""
    if req.compensation_label and req.compensation_label != "No Compensation":
        comp_note = f" Compensation if you decline: {req.compensation_label}."
    body = (
        f"{_rfa_player_name(player)} accepted an offer sheet from another team."
        f" Match or reject within 24 hours.{comp_note}\n{_rfa_offer_summary(req, player=player)}"
    )
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(rights_mem),
            kind="rfa_awaiting_match",
            title=f"RFA match decision needed (#{req.id})",
            body=body[:4000],
            article_id=int(req.id),
        )
    )
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(req.offering_user_id),
            kind="rfa_offer_completed",
            title=f"RFA accepted — awaiting match (#{req.id})",
            body=f"Player accepted. Original team has 24 hours to match or reject.\n{_rfa_offer_summary(req, player=player)}"[:4000],
            article_id=int(req.id),
        )
    )
    _commit_notifications()


def notify_rfa_original_team_decision(
    league_slug: str, req: RfaOfferRequest, *, player=None, offering_team=None
) -> None:
    matched = str(req.original_team_decision or "").strip().lower() == "match"
    team_name = offering_team.full_display_name() if offering_team else f"team {req.offering_team_id}"
    if matched:
        title = f"RFA matched by original team (#{req.id})"
        body = f"The original team matched your offer for {_rfa_player_name(player)}. The player remains with their club."
    else:
        title = f"RFA not matched — proceed (#{req.id})"
        body = f"The original team declined to match {_rfa_player_name(player)}."
        if req.compensation_label and req.compensation_label != "No Compensation":
            body += f" They owe you {req.compensation_label}."
        body += f" Coordinate roster moves with {team_name} and admins."
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(req.offering_user_id),
            kind="rfa_original_matched" if matched else "rfa_original_rejected",
            title=title[:400],
            body=body[:4000],
            article_id=int(req.id),
        )
    )
    _commit_notifications()


def notify_rfa_offer_outcome(
    league_slug: str, req: RfaOfferRequest, *, player=None, title: str, body: str
) -> None:
    _add_notification(
        GmInAppNotification(
            league_slug=league_slug,
            user_id=int(req.offering_user_id),
            kind="rfa_offer_completed",
            title=title[:400],
            body=(body or _rfa_offer_summary(req, player=player))[:4000],
            article_id=int(req.id),
        )
    )
    _commit_notifications()
