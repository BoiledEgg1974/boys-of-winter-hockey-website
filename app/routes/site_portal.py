"""GM + admin site features (league mounts only): AP, news, redemptions."""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from app.auth_login import active_membership_for_league, require_admin
from app.config import LEAGUES, league_group_for_slug, league_slugs
from app.league_db import db
from app.mail_util import send_site_email
from app.models import Player, PlayerContract, Team
from app.services.ap_multileague import team_id_for_slug_in_league
from app.services.gm_messaging import (
    active_peer_membership,
    gm_display_name,
    inbox_threads,
    list_other_active_gms,
    mark_thread_read,
    thread_messages,
)
from app.services.gm_notifications import (
    list_notifications,
    notify_news_approved,
    notify_news_denied,
    notify_redemption_approved,
    notify_redemption_denied,
)
from app.services.ap_service import (
    active_redemption_items,
    add_ledger_entry,
    approve_redemption_request,
    new_redemption_token,
    publish_news_and_maybe_award_ap,
    team_ap_balance,
)
from app.site_models import (
    ApRedemptionCatalog,
    ApRedemptionRequest,
    GmInAppNotification,
    GmLeagueMembership,
    GmLeagueMessage,
    NewsArticle,
    User,
)

site_gm_bp = Blueprint("site_gm", __name__)
site_admin_bp = Blueprint("site_admin", __name__, url_prefix="/admin")

_GM_MESSAGE_MAX_LEN = 6000


def _league_slug() -> str:
    from flask import current_app

    return str(current_app.config.get("LEAGUE_SLUG") or "")


def _membership():
    return active_membership_for_league(current_user, _league_slug())


@site_gm_bp.get("/action-points")
def action_points_page():
    slug = _league_slug()
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    rows = []
    for t in teams:
        rows.append({"team": t, "balance": team_ap_balance(slug, t.id)})
    rows.sort(key=lambda r: (-r["balance"], r["team"].name or ""))
    mem = _membership() if current_user.is_authenticated else None
    catalog = active_redemption_items(slug) if mem else []
    bal = team_ap_balance(slug, mem.team_id) if mem else None
    return render_template(
        "action_points.html",
        rows=rows,
        membership=mem,
        catalog=catalog,
        balance=bal,
    )


@site_gm_bp.post("/action-points/redeem")
@login_required
def action_points_redeem():
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("site_gm.action_points_page"))
    ids = [int(x) for x in request.form.getlist("catalog_id") if str(x).strip().isdigit()]
    if not ids:
        flash("Select at least one redemption.", "err")
        return redirect(url_for("site_gm.action_points_page"))
    items = db.session.scalars(select(ApRedemptionCatalog).where(ApRedemptionCatalog.id.in_(ids))).all()
    group = league_group_for_slug(slug)
    lines = []
    total = 0
    for it in items:
        if not it.is_active or it.league_group != group:
            continue
        lines.append({"id": it.id, "title": it.title, "cost": it.cost_ap})
        total += int(it.cost_ap)
    bal = team_ap_balance(slug, mem.team_id)
    if total <= 0 or bal < total:
        flash("Insufficient AP or invalid selection.", "err")
        return redirect(url_for("site_gm.action_points_page"))
    req = ApRedemptionRequest(
        user_id=current_user.id,
        league_slug=slug,
        team_id=mem.team_id,
        status="pending",
        lines_json=json.dumps(lines),
        total_cost=total,
        token=new_redemption_token(),
    )
    db.session.add(req)
    db.session.flush()
    admin_to = str(current_app.config.get("JOIN_LEAGUE_RECIPIENT", "")).strip()
    if admin_to:
        try:
            link = request.url_root.rstrip("/") + url_for("site_admin.ap_request_one", rid=req.id)
            send_site_email(
                subject=f"[{_league_slug()}] AP redemption request #{req.id}",
                body=f"User: {current_user.email}\nTeam id: {mem.team_id}\nTotal AP: {total}\n\nReview:\n{link}\n",
                to_addrs=[admin_to],
            )
        except Exception:
            pass
    db.session.commit()
    flash("Request submitted for administrator approval.", "ok")
    return redirect(url_for("site_gm.action_points_page"))


@site_gm_bp.route("/league-news", methods=["GET", "POST"])
@login_required
def league_news():
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        if not title or not body:
            flash("Title and body are required.", "err")
        else:
            art = NewsArticle(
                league_slug=slug,
                team_id=mem.team_id,
                title=title[:300],
                body=body,
                author_user_id=current_user.id,
                status="pending",
            )
            db.session.add(art)
            db.session.commit()
            flash("Article submitted for review.", "ok")
            return redirect(url_for("site_gm.league_news"))
    articles = db.session.scalars(
        select(NewsArticle)
        .where(NewsArticle.league_slug == slug, NewsArticle.author_user_id == current_user.id)
        .order_by(NewsArticle.created_at.desc())
        .limit(50)
    ).all()
    return render_template("league_news_gm.html", articles=articles, membership=mem)


@site_gm_bp.get("/gm-messages")
@login_required
def gm_messages_inbox():
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    threads = inbox_threads(slug, current_user.id)
    peer_ids = {t["peer_id"] for t in threads}
    peer_users: dict[int, User] = {}
    if peer_ids:
        for u in db.session.scalars(select(User).where(User.id.in_(peer_ids))).all():
            peer_users[u.id] = u
    peer_team_by_id: dict[int, Team | None] = {}
    for pid in peer_ids:
        pm = active_peer_membership(slug, pid)
        peer_team_by_id[pid] = db.session.get(Team, pm.team_id) if pm else None

    others = list_other_active_gms(slug, current_user.id)
    team_ids = {m.team_id for m, _ in others}
    teams_by_id = (
        {t.id: t for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all()} if team_ids else {}
    )
    thread_peer_ids = {t["peer_id"] for t in threads}
    other_rows: list[dict] = []
    for mrow, u in others:
        if u.id not in thread_peer_ids:
            other_rows.append({"user": u, "team": teams_by_id.get(mrow.team_id), "membership": mrow})
    other_rows.sort(key=lambda r: gm_display_name(r["user"]).lower())
    notifications = list_notifications(slug, current_user.id)
    return render_template(
        "gm_messages_inbox.html",
        membership=mem,
        notifications=notifications,
        threads=threads,
        peer_users=peer_users,
        peer_team_by_id=peer_team_by_id,
        other_rows=other_rows,
        gm_display_name=gm_display_name,
    )


@site_gm_bp.get("/gm-messages/notifications/<int:nid>/open")
@login_required
def gm_notification_open(nid: int):
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    n = db.session.get(GmInAppNotification, nid)
    if not n or n.user_id != current_user.id or n.league_slug != slug:
        abort(404)
    n.read_at = datetime.utcnow()
    db.session.commit()
    if n.kind == "news_approved" and n.article_id:
        return redirect(url_for("main.league_headlines") + f"#a{n.article_id}")
    if n.kind == "news_denied":
        return redirect(url_for("site_gm.league_news"))
    if n.kind == "redemption_approved":
        return redirect(url_for("site_gm.action_points_page"))
    if n.kind == "redemption_denied":
        return redirect(url_for("site_gm.action_points_page"))
    return redirect(url_for("site_gm.gm_messages_inbox"))


@site_gm_bp.route("/gm-messages/with/<int:peer_user_id>", methods=["GET", "POST"])
@login_required
def gm_messages_thread(peer_user_id: int):
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    if peer_user_id == current_user.id:
        abort(404)
    if not active_peer_membership(slug, peer_user_id):
        flash("That user is not an active GM in this league.", "err")
        return redirect(url_for("site_gm.gm_messages_inbox"))
    peer = db.session.get(User, peer_user_id)
    if not peer:
        abort(404)
    peer_mem = active_peer_membership(slug, peer_user_id)
    peer_team = db.session.get(Team, peer_mem.team_id) if peer_mem else None
    my_team = db.session.get(Team, mem.team_id)

    if request.method == "POST":
        body = (request.form.get("body") or "").strip()
        if not body:
            flash("Message cannot be empty.", "err")
        elif len(body) > _GM_MESSAGE_MAX_LEN:
            flash(f"Message is too long (max {_GM_MESSAGE_MAX_LEN} characters).", "err")
        else:
            db.session.add(
                GmLeagueMessage(
                    league_slug=slug,
                    from_user_id=current_user.id,
                    to_user_id=peer_user_id,
                    body=body[:_GM_MESSAGE_MAX_LEN],
                )
            )
            db.session.commit()
            flash("Sent.", "ok")
        return redirect(url_for("site_gm.gm_messages_thread", peer_user_id=peer_user_id))

    mark_thread_read(slug, current_user.id, peer_user_id)
    db.session.commit()
    messages = thread_messages(slug, current_user.id, peer_user_id)
    return render_template(
        "gm_messages_thread.html",
        membership=mem,
        peer=peer,
        peer_team=peer_team,
        my_team=my_team,
        messages=messages,
        gm_display_name=gm_display_name,
    )


@site_admin_bp.get("/")
@login_required
def admin_home():
    require_admin()
    slug = _league_slug()
    return render_template(
        "admin_site_home.html",
        league_slug=slug,
    )


@site_admin_bp.get("/news")
@login_required
def admin_news_queue():
    require_admin()
    slug = _league_slug()
    rows = list(
        db.session.scalars(
            select(NewsArticle)
            .where(NewsArticle.league_slug == slug)
            .order_by(NewsArticle.created_at.desc())
            .limit(100)
        ).all()
    )
    author_ids = {a.author_user_id for a in rows}
    news_authors_by_id: dict[int, User] = {}
    if author_ids:
        for u in db.session.scalars(select(User).where(User.id.in_(author_ids))).all():
            news_authors_by_id[u.id] = u
    team_ids = {a.team_id for a in rows if a.team_id}
    news_teams_by_id: dict[int, Team] = {}
    if team_ids:
        for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
            news_teams_by_id[t.id] = t
    return render_template(
        "admin_news_queue.html",
        articles=rows,
        news_authors_by_id=news_authors_by_id,
        news_teams_by_id=news_teams_by_id,
    )


@site_admin_bp.get("/news/<int:aid>/preview")
@login_required
def admin_news_preview(aid: int):
    require_admin()
    slug = _league_slug()
    art = db.session.get(NewsArticle, aid)
    if not art or art.league_slug != slug:
        abort(404)
    author = db.session.get(User, art.author_user_id)
    team = db.session.get(Team, art.team_id) if art.team_id else None
    return render_template(
        "admin_news_preview.html",
        article=art,
        author=author,
        team=team,
    )


@site_admin_bp.post("/news/<int:aid>/publish")
@login_required
def admin_news_publish(aid: int):
    require_admin()
    slug = _league_slug()
    art = db.session.get(NewsArticle, aid)
    if not art or art.league_slug != slug:
        abort(404)
    if art.status != "pending":
        flash("That submission was already processed.", "err")
        return redirect(url_for("site_admin.admin_news_queue"))
    pts = int(current_app.config.get("NEWS_ARTICLE_AP_POINTS", 3))
    publish_news_and_maybe_award_ap(art, points=pts)
    notify_news_approved(slug, art)
    flash(
        "Approved. It appears on the home page under Around the League. The author was notified in GM Messages.",
        "ok",
    )
    return redirect(url_for("site_admin.admin_news_queue"))


@site_admin_bp.post("/news/<int:aid>/reject")
@login_required
def admin_news_reject(aid: int):
    require_admin()
    slug = _league_slug()
    art = db.session.get(NewsArticle, aid)
    if not art or art.league_slug != slug:
        abort(404)
    if art.status != "pending":
        flash("That submission was already processed.", "err")
        return redirect(url_for("site_admin.admin_news_queue"))
    art.status = "rejected"
    db.session.commit()
    notify_news_denied(slug, art)
    flash("Denied. The author was notified in GM Messages (no email).", "ok")
    return redirect(url_for("site_admin.admin_news_queue"))


@site_admin_bp.route("/ap-ledger/export-multileague", methods=["POST"])
@login_required
def admin_ap_export_multileague():
    """Award +1 AP for each selected franchise (team slug) in every mounted league DB."""
    require_admin()
    raw = request.form.getlist("team_slug")
    team_slugs = list(dict.fromkeys(s.strip() for s in raw if s and s.strip()))
    if not team_slugs:
        flash("Select at least one team.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    leagues = league_slugs()
    note = "EXPORT: +1 AP all leagues"
    added = 0
    for league_slug in leagues:
        for team_slug in team_slugs:
            tid = team_id_for_slug_in_league(league_slug, team_slug)
            if tid is None:
                continue
            add_ledger_entry(
                league_slug=league_slug,
                team_id=tid,
                delta=1,
                reason_code="manual",
                meta={"note": note, "team_slug": team_slug},
                created_by_user_id=current_user.id,
            )
            added += 1
    db.session.commit()
    if added:
        flash(
            f"EXPORT: added {added} ledger row(s) (+1 AP per team in each league where that franchise exists).",
            "ok",
        )
    else:
        flash("No matching teams found in any league database for the selection.", "err")
    return redirect(url_for("site_admin.admin_ap_ledger"))


_BATCH_AP_REASONS: dict[str, str] = {
    "batch_all_star": "ALL-STAR",
    "batch_skills": "SKILLS",
    "batch_award": "AWARD",
    "batch_predictions": "PREDICTIONS",
    "batch_penalties": "PENALTIES",
}


@site_admin_bp.post("/ap-ledger/batch-adjust")
@login_required
def admin_ap_batch_adjust():
    """Apply per-team AP deltas from a modal, for every mounted league (matched by team slug)."""
    require_admin()
    reason = (request.form.get("reason_code") or "").strip()
    if reason not in _BATCH_AP_REASONS:
        flash("Invalid batch type.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    teams = list(db.session.scalars(select(Team)).all())
    allowed_slugs = {t.slug for t in teams}
    leagues = league_slugs()
    label = _BATCH_AP_REASONS[reason]

    if reason == "batch_predictions":
        picked = list(
            dict.fromkeys(s.strip() for s in request.form.getlist("team_slug") if s and s.strip())
        )
        if not picked:
            flash("PREDICTIONS: select at least one team.", "err")
            return redirect(url_for("site_admin.admin_ap_ledger"))
        entries = 0
        for team_slug in picked:
            if team_slug not in allowed_slugs:
                continue
            for league_slug in leagues:
                tid = team_id_for_slug_in_league(league_slug, team_slug)
                if tid is None:
                    continue
                add_ledger_entry(
                    league_slug=league_slug,
                    team_id=tid,
                    delta=1,
                    reason_code=reason,
                    meta={"batch": label, "team_slug": team_slug},
                    created_by_user_id=current_user.id,
                )
                entries += 1
        db.session.commit()
        if entries:
            flash(
                f"PREDICTIONS: added {entries} ledger row(s) (+1 AP per checked team in each league where that slug exists).",
                "ok",
            )
        else:
            flash("PREDICTIONS: no matching teams in league databases for that selection.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))

    prefix = "d_"
    entries = 0
    for key, raw in request.form.items():
        if not key.startswith(prefix):
            continue
        team_slug = key[len(prefix) :]
        if team_slug not in allowed_slugs:
            continue
        s = str(raw).strip()
        if not s:
            continue
        try:
            val = int(s)
        except ValueError:
            flash(f"Invalid number for team «{team_slug}».", "err")
            return redirect(url_for("site_admin.admin_ap_ledger"))
        if val == 0:
            continue
        if reason == "batch_penalties":
            delta = -abs(val)
        else:
            delta = val
        for league_slug in leagues:
            tid = team_id_for_slug_in_league(league_slug, team_slug)
            if tid is None:
                continue
            add_ledger_entry(
                league_slug=league_slug,
                team_id=tid,
                delta=delta,
                reason_code=reason,
                meta={"batch": label, "team_slug": team_slug},
                created_by_user_id=current_user.id,
            )
            entries += 1
    db.session.commit()
    if entries:
        flash(
            f"{label}: wrote {entries} ledger row(s) across leagues (non-zero inputs only; "
            f"franchises matched by slug).",
            "ok",
        )
    else:
        flash(f"{label}: enter at least one non-zero amount.", "err")
    return redirect(url_for("site_admin.admin_ap_ledger"))


@site_admin_bp.route("/ap-ledger", methods=["GET", "POST"])
@login_required
def admin_ap_ledger():
    require_admin()
    slug = _league_slug()
    if request.method == "POST":
        try:
            tid = int(request.form.get("team_id") or "0")
            delta = int(request.form.get("delta") or "0")
        except ValueError:
            flash("Invalid numbers.", "err")
            return redirect(url_for("site_admin.admin_ap_ledger"))
        note = (request.form.get("note") or "").strip()
        if tid and delta:
            add_ledger_entry(
                league_slug=slug,
                team_id=tid,
                delta=delta,
                reason_code="manual",
                meta={"note": note},
                created_by_user_id=current_user.id,
            )
            db.session.commit()
            flash("Ledger entry added.", "ok")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    team_rows = [{"team": t, "balance": team_ap_balance(slug, t.id)} for t in teams]
    team_rows.sort(key=lambda r: (r["team"].name or "").lower())
    return render_template(
        "admin_ap_ledger.html",
        teams=teams,
        team_rows=team_rows,
        leagues_registry=LEAGUES,
    )


@site_admin_bp.get("/ap-requests")
@login_required
def admin_ap_requests():
    require_admin()
    slug = _league_slug()
    rows = db.session.scalars(
        select(ApRedemptionRequest)
        .where(ApRedemptionRequest.league_slug == slug, ApRedemptionRequest.status == "pending")
        .order_by(ApRedemptionRequest.created_at.desc())
    ).all()
    team_ids = {r.team_id for r in rows if r.team_id}
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        teams_by_id = {t.id: t for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all()}
    queue_rows: list[dict] = []
    for r in rows:
        titles: list[str] = []
        try:
            items = json.loads(r.lines_json or "[]")
            if isinstance(items, list):
                for it in items:
                    title = str((it or {}).get("title") or "").strip()
                    if title:
                        cost = (it or {}).get("cost")
                        if cost is None:
                            titles.append(title)
                        else:
                            titles.append(f"{title} ({cost} AP)")
        except Exception:
            pass
        queue_rows.append(
            {
                "req": r,
                "team": teams_by_id.get(r.team_id),
                "redemption_items": titles,
                "redemption": ", ".join(titles) if titles else "Custom redemption",
                "balance": team_ap_balance(slug, r.team_id),
            }
        )
    return render_template("admin_ap_requests.html", queue_rows=queue_rows)


@site_admin_bp.get("/ap-requests/<int:rid>")
@login_required
def ap_request_one(rid: int):
    require_admin()
    slug = _league_slug()
    req = db.session.get(ApRedemptionRequest, rid)
    if not req or req.league_slug != slug:
        abort(404)
    return render_template("admin_ap_request_detail.html", req=req)


@site_admin_bp.post("/ap-requests/<int:rid>/approve")
@login_required
def admin_ap_approve(rid: int):
    require_admin()
    slug = _league_slug()
    req = db.session.get(ApRedemptionRequest, rid)
    if not req or req.league_slug != slug or req.status != "pending":
        abort(404)
    ok = approve_redemption_request(req, current_user.id)
    if ok:
        try:
            line_items = json.loads(req.lines_json or "[]")
        except Exception:
            line_items = []
        team = db.session.get(Team, req.team_id)
        body_parts = []
        if isinstance(line_items, list):
            for it in line_items:
                title = str((it or {}).get("title") or "").strip()
                cost = (it or {}).get("cost")
                if title:
                    body_parts.append(f"- {title}" + (f" ({cost} AP)" if cost is not None else ""))
        red_label = ", ".join([p.replace("- ", "", 1) for p in body_parts]) if body_parts else f"Request #{req.id}"
        db.session.add(
            NewsArticle(
                league_slug=slug,
                team_id=req.team_id,
                title=f"AP Redemption Approved — {team.full_display_name() if team else f'Team {req.team_id}'}",
                body=(
                    f"Redemption approved: {red_label}\n"
                    f"AP deducted: {int(req.total_cost)}\n"
                    f"Processed by admin."
                ),
                author_user_id=req.user_id,
                status="published",
                published_at=datetime.utcnow(),
            )
        )
        db.session.commit()
        notify_redemption_approved(slug, req)
        flash("Approved, AP deducted, GM notified in-app, and transaction posted to Around the League.", "ok")
    else:
        flash("Insufficient balance at approval time.", "err")
    return redirect(url_for("site_admin.admin_ap_requests"))


@site_admin_bp.post("/ap-requests/<int:rid>/deny")
@login_required
def admin_ap_deny(rid: int):
    require_admin()
    slug = _league_slug()
    req = db.session.get(ApRedemptionRequest, rid)
    if not req or req.league_slug != slug or req.status != "pending":
        abort(404)
    req.status = "denied"
    req.processed_at = datetime.utcnow()
    db.session.commit()
    notify_redemption_denied(slug, req)
    flash("Request denied and GM notified in-app.", "ok")
    return redirect(url_for("site_admin.admin_ap_requests"))


@site_admin_bp.route("/catalog", methods=["GET", "POST"])
@login_required
def admin_catalog():
    require_admin()
    slug = _league_slug()
    group = league_group_for_slug(slug)
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        desc = (request.form.get("description") or "").strip()
        try:
            cost = int(request.form.get("cost_ap") or "0")
            sort_order = int(request.form.get("sort_order") or "0")
        except ValueError:
            cost, sort_order = 0, 0
        if title and cost > 0:
            db.session.add(
                ApRedemptionCatalog(
                    league_group=group,
                    sort_order=sort_order,
                    title=title[:400],
                    description=desc,
                    cost_ap=cost,
                    is_active=True,
                )
            )
            db.session.commit()
        return redirect(url_for("site_admin.admin_catalog"))
    rows = db.session.scalars(
        select(ApRedemptionCatalog)
        .where(ApRedemptionCatalog.league_group == group)
        .order_by(ApRedemptionCatalog.cost_ap, ApRedemptionCatalog.sort_order, ApRedemptionCatalog.id)
    ).all()
    return render_template("admin_catalog.html", rows=rows, league_group=group)


@site_admin_bp.post("/catalog/<int:cid>/toggle")
@login_required
def admin_catalog_toggle(cid: int):
    require_admin()
    slug = _league_slug()
    group = league_group_for_slug(slug)
    row = db.session.get(ApRedemptionCatalog, cid)
    if row and row.league_group == group:
        row.is_active = not row.is_active
        db.session.commit()
    return redirect(url_for("site_admin.admin_catalog"))


@site_admin_bp.route("/contract", methods=["GET", "POST"])
@login_required
def admin_contract_edit():
    require_admin()
    slug = _league_slug()
    if request.method == "POST":
        try:
            pid = int(request.form.get("player_id") or "0")
            salary = int(request.form.get("average_salary") or "0")
        except ValueError:
            flash("Invalid player or salary.", "err")
            return redirect(url_for("site_admin.admin_contract_edit"))
        pl = db.session.get(Player, pid)
        if not pl:
            flash("Player not found.", "err")
            return redirect(url_for("site_admin.admin_contract_edit"))
        c = db.session.scalar(select(PlayerContract).where(PlayerContract.player_id == pid).limit(1))
        if not c:
            c = PlayerContract(player_id=pid, average_salary=salary)
            db.session.add(c)
        else:
            c.average_salary = salary
        db.session.commit()
        flash("Contract salary updated.", "ok")
        return redirect(url_for("site_admin.admin_contract_edit"))
    return render_template("admin_contract.html", league_slug=slug)
