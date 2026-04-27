"""GM + admin site features (league mounts only): AP, news, redemptions."""
from __future__ import annotations

import json
from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select
from app.auth_login import active_membership_for_league, require_admin
from app.config import league_display_name, league_group_for_slug
from app.league_db import db
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
    notify_all_gms_admin_article,
    notify_news_approved,
    notify_news_denied,
    notify_redemption_approved,
    notify_redemption_denied,
)
from app.services.news_categories import (
    NEWS_CATEGORY_ADMIN_SUBMISSION,
    NEWS_CATEGORY_CHOICES_ADMIN,
    NEWS_CATEGORY_CHOICES_GM,
    normalize_news_category,
    news_category_label,
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


def _can_use_gm_messaging() -> bool:
    """Active GMs and site admins may use the in-league GM messages inbox."""
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, "is_admin", False):
        return True
    return _membership() is not None


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
    try:
        from app.config import league_display_name as _league_display_name
        from app.services.admin_review_notify import notify_ap_redemption_pending

        notify_ap_redemption_pending(
            league_slug=slug,
            league_display_name=_league_display_name(slug),
            request_id=int(req.id),
            user_email=str(current_user.email or ""),
            team_id=int(mem.team_id),
            total_ap=int(total),
        )
    except Exception as exc:
        current_app.logger.warning("Admin notify (AP redemption): %s", exc)
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
        cat = normalize_news_category(request.form.get("category"), allow_admin=False)
        if not title or not body:
            flash("Title and body are required.", "err")
        elif not cat:
            flash("Choose a valid category.", "err")
        else:
            upload = request.files.get("image")
            if upload and upload.filename:
                from app.services.news_article_media import ext_from_upload_filename

                if ext_from_upload_filename(upload.filename) is None:
                    flash("Image must be PNG, JPEG, WebP, or GIF.", "err")
                    return redirect(url_for("site_gm.league_news"))
            art = NewsArticle(
                league_slug=slug,
                team_id=mem.team_id,
                title=title[:300],
                body=body,
                category=cat,
                author_user_id=current_user.id,
                status="pending",
            )
            db.session.add(art)
            db.session.flush()
            if upload and upload.filename:
                from app.services.news_article_media import save_news_article_image

                rel = save_news_article_image(upload, league_slug=slug, article_id=art.id)
                if not rel:
                    db.session.rollback()
                    flash("Image could not be saved (max 2.5 MB).", "err")
                    return redirect(url_for("site_gm.league_news"))
                art.image_rel_path = rel
            db.session.commit()
            try:
                from app.services.admin_review_notify import notify_news_pending_review

                notify_news_pending_review(
                    league_slug=slug,
                    league_display_name=str(current_app.config.get("LEAGUE_DISPLAY_NAME", slug)),
                    article_id=int(art.id),
                    author_email=str(current_user.email or ""),
                    title=str(art.title or ""),
                )
                db.session.commit()
            except Exception as exc:
                current_app.logger.warning("Admin notify (news pending): %s", exc)
                db.session.rollback()
            flash("Article submitted for review.", "ok")
            return redirect(url_for("site_gm.league_news"))
    articles = db.session.scalars(
        select(NewsArticle)
        .where(NewsArticle.league_slug == slug, NewsArticle.author_user_id == current_user.id)
        .order_by(NewsArticle.created_at.desc())
        .limit(50)
    ).all()
    return render_template(
        "league_news_gm.html",
        articles=articles,
        membership=mem,
        news_category_choices=NEWS_CATEGORY_CHOICES_GM,
        news_category_label=news_category_label,
    )


@site_gm_bp.get("/gm-messages")
@login_required
def gm_messages_inbox():
    slug = _league_slug()
    mem = _membership()
    if not _can_use_gm_messaging():
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
    compose_recipients: list[dict[str, object]] = []
    for mrow, u in others:
        tm = teams_by_id.get(mrow.team_id)
        name = gm_display_name(u)
        suffix = tm.full_display_name() if tm else ""
        label = f"{name} — {suffix}" if suffix else name
        compose_recipients.append(
            {
                "user_id": u.id,
                "label": label,
                "thread_url": url_for("site_gm.gm_messages_thread", peer_user_id=u.id),
            }
        )
    compose_recipients.sort(key=lambda r: str(r["label"]).lower())
    notifications = list_notifications(slug, current_user.id)
    return render_template(
        "gm_messages_inbox.html",
        membership=mem,
        notifications=notifications,
        threads=threads,
        peer_users=peer_users,
        peer_team_by_id=peer_team_by_id,
        other_rows=other_rows,
        compose_recipients=compose_recipients,
        gm_display_name=gm_display_name,
    )


@site_gm_bp.get("/gm-messages/notifications/<int:nid>/open")
@login_required
def gm_notification_open(nid: int):
    slug = _league_slug()
    if not _can_use_gm_messaging():
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    n = db.session.get(GmInAppNotification, nid)
    if not n or n.user_id != current_user.id or n.league_slug != slug:
        abort(404)
    n.read_at = datetime.utcnow()
    db.session.commit()
    if n.kind == "news_approved" and n.article_id:
        return redirect(url_for("main.league_headlines") + f"#a{n.article_id}")
    if n.kind == "admin_league_article" and n.article_id:
        return redirect(url_for("main.league_headlines") + f"#a{n.article_id}")
    if n.kind == "news_denied":
        return redirect(url_for("site_gm.league_news"))
    if n.kind == "redemption_approved":
        return redirect(url_for("site_gm.action_points_page"))
    if n.kind == "redemption_denied":
        return redirect(url_for("site_gm.action_points_page"))
    if n.kind == "admin_review_news" and n.article_id:
        return redirect(url_for("site_admin.admin_news_preview", aid=int(n.article_id)))
    if n.kind == "admin_review_ap" and n.article_id:
        return redirect(url_for("site_admin.ap_request_one", rid=int(n.article_id)))
    return redirect(url_for("site_gm.gm_messages_inbox"))


@site_gm_bp.route("/gm-messages/with/<int:peer_user_id>", methods=["GET", "POST"])
@login_required
def gm_messages_thread(peer_user_id: int):
    slug = _league_slug()
    mem = _membership()
    if not _can_use_gm_messaging():
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
    my_team = db.session.get(Team, mem.team_id) if mem else None

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


@site_admin_bp.route("/news/compose", methods=["GET", "POST"])
@login_required
def admin_news_compose():
    """Publish a headline immediately as the league office (no moderation, no AP grant)."""
    require_admin()
    slug = _league_slug()
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        raw_tid = (request.form.get("team_id") or "").strip()
        cat = normalize_news_category(request.form.get("category"), allow_admin=True)
        if not title or not body:
            flash("Title and body are required.", "err")
            return render_template(
                "admin_news_compose.html",
                teams=teams,
                category_choices=NEWS_CATEGORY_CHOICES_ADMIN,
                form_title=title,
                form_body=body,
                form_team_id=raw_tid,
                form_category=cat or (request.form.get("category") or "").strip(),
            )
        if not cat:
            flash("Choose a category.", "err")
            return render_template(
                "admin_news_compose.html",
                teams=teams,
                category_choices=NEWS_CATEGORY_CHOICES_ADMIN,
                form_title=title,
                form_body=body,
                form_team_id=raw_tid,
                form_category=(request.form.get("category") or "").strip(),
            )
        if not raw_tid.isdigit():
            flash("Select a team this article is about.", "err")
            return render_template(
                "admin_news_compose.html",
                teams=teams,
                category_choices=NEWS_CATEGORY_CHOICES_ADMIN,
                form_title=title,
                form_body=body,
                form_team_id=raw_tid,
                form_category=cat,
            )
        team_id = int(raw_tid)
        team = db.session.get(Team, team_id)
        if not team:
            flash("Invalid team.", "err")
            return render_template(
                "admin_news_compose.html",
                teams=teams,
                category_choices=NEWS_CATEGORY_CHOICES_ADMIN,
                form_title=title,
                form_body=body,
                form_team_id=raw_tid,
                form_category=cat,
            )
        upload = request.files.get("image")
        if upload and upload.filename:
            from app.services.news_article_media import ext_from_upload_filename

            if ext_from_upload_filename(upload.filename) is None:
                flash("Image must be PNG, JPEG, WebP, or GIF.", "err")
                return render_template(
                    "admin_news_compose.html",
                    teams=teams,
                    category_choices=NEWS_CATEGORY_CHOICES_ADMIN,
                    form_title=title,
                    form_body=body,
                    form_team_id=raw_tid,
                    form_category=cat,
                )
        art = NewsArticle(
            league_slug=slug,
            team_id=team_id,
            title=title[:300],
            body=body,
            category=cat,
            author_user_id=current_user.id,
            status="published",
            published_at=datetime.utcnow(),
            ap_awarded=False,
        )
        db.session.add(art)
        db.session.flush()
        if upload and upload.filename:
            from app.services.news_article_media import save_news_article_image

            rel = save_news_article_image(upload, league_slug=slug, article_id=art.id)
            if not rel:
                db.session.rollback()
                flash("Image could not be saved (max 2.5 MB).", "err")
                return render_template(
                    "admin_news_compose.html",
                    teams=teams,
                    category_choices=NEWS_CATEGORY_CHOICES_ADMIN,
                    form_title=title,
                    form_body=body,
                    form_team_id=raw_tid,
                    form_category=cat,
                )
            art.image_rel_path = rel
        db.session.commit()
        if cat == NEWS_CATEGORY_ADMIN_SUBMISSION:
            notify_all_gms_admin_article(slug, art)
            flash(
                "Article published and sent to every active GM in GM Messages (notifications).",
                "ok",
            )
        else:
            flash("Article published. It appears on the home page under Around the League.", "ok")
        return redirect(url_for("site_admin.admin_news_queue"))
    return render_template(
        "admin_news_compose.html",
        teams=teams,
        category_choices=NEWS_CATEGORY_CHOICES_ADMIN,
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
        news_category_label=news_category_label,
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
        news_category_label=news_category_label,
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
    """Award +1 AP for each selected team in the current league only (URL mount)."""
    require_admin()
    cur_slug = _league_slug()
    raw = request.form.getlist("team_slug")
    team_slugs = list(dict.fromkeys(s.strip() for s in raw if s and s.strip()))
    if not team_slugs:
        flash("Select at least one team.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    label = league_display_name(cur_slug)
    note = f"EXPORT: +1 AP ({label})"
    added = 0
    for team_slug in team_slugs:
        tid = team_id_for_slug_in_league(
            cur_slug,
            team_slug,
            orm_session=db.session,
            orm_league_slug=cur_slug,
        )
        if tid is None:
            continue
        add_ledger_entry(
            league_slug=cur_slug,
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
            f"EXPORT: added {added} ledger row(s) (+1 AP per team in {label} only).",
            "ok",
        )
    else:
        flash(
            f"No matching teams in this league ({label}) for the selection.",
            "err",
        )
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
    """Apply per-team AP deltas from a modal for the current league only (URL mount)."""
    require_admin()
    cur_slug = _league_slug()
    league_name = league_display_name(cur_slug)
    reason = (request.form.get("reason_code") or "").strip()
    if reason not in _BATCH_AP_REASONS:
        flash("Invalid batch type.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    teams = list(db.session.scalars(select(Team)).all())
    allowed_slugs = {t.slug for t in teams}
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
            tid = team_id_for_slug_in_league(
                cur_slug,
                team_slug,
                orm_session=db.session,
                orm_league_slug=cur_slug,
            )
            if tid is None:
                continue
            add_ledger_entry(
                league_slug=cur_slug,
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
                f"PREDICTIONS: added {entries} ledger row(s) (+1 AP per checked team in {league_name} only).",
                "ok",
            )
        else:
            flash("PREDICTIONS: no matching teams in this league for that selection.", "err")
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
        tid = team_id_for_slug_in_league(
            cur_slug,
            team_slug,
            orm_session=db.session,
            orm_league_slug=cur_slug,
        )
        if tid is None:
            continue
        add_ledger_entry(
            league_slug=cur_slug,
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
            f"{label}: wrote {entries} ledger row(s) in {league_name} only "
            f"(non-zero inputs; team slugs as shown on this page).",
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
                category="transactions",
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
