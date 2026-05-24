"""Notify site administrators when something needs review (email + optional in-app)."""
from __future__ import annotations

import os
from typing import Iterable

from flask import current_app, has_request_context, url_for
from sqlalchemy import select

from app.league_db import db
from app.mail_util import send_site_email
from app.site_models import GmInAppNotification, User


def admin_review_email_addresses() -> list[str]:
    """Primary join-league / ops inbox plus optional comma-separated ADMIN_ALERT_EMAILS."""
    primary = str(current_app.config.get("JOIN_LEAGUE_RECIPIENT", "") or "").strip()
    extra = str(current_app.config.get("ADMIN_ALERT_EMAILS", "") or "").strip()
    if not extra:
        extra = os.environ.get("ADMIN_ALERT_EMAILS", "") or ""
    out: list[str] = []
    if primary:
        out.append(primary)
    for part in extra.split(","):
        p = part.strip()
        if not p:
            continue
        if p.lower() not in {x.lower() for x in out}:
            out.append(p)
    return out


def try_send_admin_review_email(*, subject: str, body: str) -> None:
    """Best-effort SMTP to all admin review addresses; logs and skips if mail is not configured."""
    addrs = admin_review_email_addresses()
    if not addrs:
        current_app.logger.info("Admin review email skipped: no JOIN_LEAGUE_RECIPIENT / ADMIN_ALERT_EMAILS.")
        return
    try:
        send_site_email(subject=subject, body=body, to_addrs=addrs)
    except Exception as exc:
        current_app.logger.warning("Admin review email failed: %s", exc)


def queue_site_admin_in_app_notifications(
    *,
    league_slug: str,
    kind: str,
    title: str,
    body: str,
    article_id: int | None = None,
) -> None:
    """Insert unread GM Messages notifications for every site admin (league-scoped). Caller should commit."""
    admin_ids = db.session.scalars(select(User.id).where(User.is_admin.is_(True))).all()
    body_trim = (body or "").strip()
    if len(body_trim) > 1900:
        body_trim = body_trim[:1900] + "…"
    for uid in admin_ids:
        notif = GmInAppNotification(
            league_slug=league_slug,
            user_id=int(uid),
            kind=kind,
            title=title[:400],
            body=body_trim,
            article_id=article_id,
        )
        db.session.add(notif)
        db.session.flush()
        try:
            from app.services.discord_direct_messages import enqueue_notification_dm

            enqueue_notification_dm(
                db.session,
                league_slug=league_slug,
                notification=notif,
            )
        except Exception:
            pass


def _abs_url_for(endpoint: str, **values: object) -> str:
    if has_request_context():
        return str(url_for(endpoint, _external=True, **values))
    return str(url_for(endpoint, **values))


def notify_news_pending_review(
    *,
    league_slug: str,
    league_display_name: str,
    article_id: int,
    author_email: str,
    title: str,
) -> None:
    try:
        preview_url = _abs_url_for("site_admin.admin_news_preview", aid=article_id)
    except Exception:
        preview_url = f"(open site admin → News queue → article #{article_id})"
    subject = f"[{league_display_name}] News article pending review (#{article_id})"
    body = (
        f"A GM submitted an Around the League article for review.\n\n"
        f"League: {league_display_name} ({league_slug})\n"
        f"Article id: {article_id}\n"
        f"Title: {title}\n"
        f"Author email: {author_email}\n\n"
        f"Review (preview):\n{preview_url}\n"
    )
    try_send_admin_review_email(subject=subject, body=body)
    queue_site_admin_in_app_notifications(
        league_slug=league_slug,
        kind="admin_review_news",
        title=f"News pending review: {title[:120]}",
        body=f"Preview and approve or deny.\n{preview_url}",
        article_id=article_id,
    )


def notify_ap_redemption_pending(
    *,
    league_slug: str,
    league_display_name: str,
    request_id: int,
    user_email: str,
    gm_name: str = "",
    team_name: str = "",
    team_id: int,
    total_ap: int,
) -> None:
    try:
        detail_url = _abs_url_for("site_admin.ap_request_one", rid=request_id)
    except Exception:
        detail_url = f"(admin → AP requests → #{request_id})"
    subject = f"[{league_display_name}] AP redemption request #{request_id}"
    gm_label = (gm_name or user_email or "GM").strip()
    team_label = (team_name or f"Team {team_id}").strip()
    body = (
        f"A GM submitted an AP redemption for approval.\n\n"
        f"League: {league_display_name} ({league_slug})\n"
        f"Request id: {request_id}\n"
        f"GM: {gm_label}\n"
        f"Team: {team_label}\n"
        f"Email: {user_email}\n"
        f"Total AP: {total_ap}\n\n"
        f"Review:\n{detail_url}\n"
    )
    try_send_admin_review_email(subject=subject, body=body)
    queue_site_admin_in_app_notifications(
        league_slug=league_slug,
        kind="admin_review_ap",
        title=f"AP redemption pending (#{request_id})",
        body=f"{gm_label} · {team_label} · {total_ap} AP\n{detail_url}",
        article_id=request_id,
    )


def notify_membership_registration_pending(
    *,
    user_email: str,
    discord_name: str,
    membership_rows: Iterable[tuple[str, int] | tuple[str, int, str | None]],
) -> None:
    """Hub registration: email only (memberships are not tied to a single league slug)."""
    try:
        hub_url = _abs_url_for("hub_auth.admin_memberships")
    except Exception:
        hub_url = "/admin/memberships"
    lines = [f"New account registration with pending membership(s).\n", f"Email: {user_email}", f"Discord: {discord_name}", ""]
    for row in membership_rows:
        if len(row) == 3:
            slug, tid, fhm = str(row[0]), int(row[1]), row[2]
        else:
            slug, tid, fhm = str(row[0]), int(row[1]), None
        fhm_s = (str(fhm).strip() if fhm is not None else "") or ""
        fhm_part = f" · FHM franchise id {fhm_s}" if fhm_s else ""
        lines.append(f"  - {slug} · DB teams.id {tid}{fhm_part}")
    lines.extend(["", f"Review memberships:", hub_url, ""])
    subject = "[Boys of Winter] New membership(s) pending approval"
    try_send_admin_review_email(subject=subject, body="\n".join(lines))


def notify_membership_change_pending(
    *,
    action: str,
    user_email: str,
    discord_name: str,
    league_slug: str,
    team_id: int,
    team_label: str,
    fhm_team_id: str = "",
    membership_id: int | None = None,
) -> None:
    try:
        hub_url = _abs_url_for("hub_auth.admin_memberships")
    except Exception:
        hub_url = "/admin/memberships"
    action_l = "remove" if action == "remove" else "add"
    action_title = "Removal" if action_l == "remove" else "Add"
    fhm_part = f" · FHM franchise id {fhm_team_id}" if fhm_team_id else ""
    subject = f"[Boys of Winter] Membership {action_l} request pending"
    body = "\n".join(
        [
            f"GM membership {action_l} request pending admin review.",
            "",
            f"Email: {user_email}",
            f"Discord: {discord_name}",
            f"League: {league_slug}",
            f"Team: {team_label} · DB teams.id {team_id}{fhm_part}",
            "",
            f"Review memberships: {hub_url}",
        ]
    )
    try_send_admin_review_email(subject=subject, body=body)
    queue_site_admin_in_app_notifications(
        league_slug=league_slug,
        kind="admin_review_membership",
        title=f"Membership {action_title} Request",
        body=body,
        article_id=membership_id,
    )


def notify_staff_change_pending(
    *,
    league_slug: str,
    league_display_name: str,
    request_id: int,
    user_email: str,
    team_id: int,
    request_type: str,
    staff_name: str,
    role_label: str,
) -> None:
    try:
        detail_url = _abs_url_for("site_admin.admin_staff_request_one", rid=request_id)
    except Exception:
        detail_url = f"(admin → Staff requests → #{request_id})"
    action = "hire" if str(request_type).strip() == "hire" else "fire"
    subject = f"[{league_display_name}] Staff {action} request #{request_id}"
    body = (
        f"A GM submitted a staff {action} request for approval.\n\n"
        f"League: {league_display_name} ({league_slug})\n"
        f"Request id: {request_id}\n"
        f"User: {user_email}\n"
        f"Team id: {team_id}\n"
        f"Staff: {staff_name}\n"
        f"Role: {role_label}\n\n"
        f"Review:\n{detail_url}\n"
    )
    try_send_admin_review_email(subject=subject, body=body)
    queue_site_admin_in_app_notifications(
        league_slug=league_slug,
        kind="admin_review_staff",
        title=f"Staff {action} pending (#{request_id})",
        body=f"{staff_name} · {role_label} · {user_email}\n{detail_url}",
        article_id=request_id,
    )
