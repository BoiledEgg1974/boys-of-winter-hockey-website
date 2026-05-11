"""GM + admin site features (league mounts only): AP, news, redemptions."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
import secrets
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import joinedload
from app.auth_login import (
    ADMIN_ROLE_CONTENT,
    ADMIN_ROLE_LEAGUE,
    ADMIN_ROLE_SUPER,
    ADMIN_ROLE_STATS,
    ADMIN_ROLE_VALUES,
    active_membership_for_league,
    require_admin,
    require_admin_role,
)
from app.config import Config, league_display_name, league_group_for_slug
from app.logo_urls import team_logo_url_for_team
from app.league_db import db
from app.models import Player, PlayerContract, Prospect, Season, Team
from app.services.ap_multileague import team_id_for_slug_in_league
from app.services.all_time_records import bowl_nhl_league_ids
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
    notify_trade_outcome_partner,
    notify_trade_outcome_proposer,
    notify_trade_proposal_commissioners,
    notify_trade_proposal_partner,
)
from app.services.news_categories import (
    NEWS_CATEGORY_ADMIN_SUBMISSION,
    NEWS_CATEGORY_CHOICES_ADMIN,
    NEWS_CATEGORY_CHOICES_GM,
    normalize_news_category,
    news_category_label,
)
from app.services.homepage_modules import (
    ALLOWED_HOMEPAGE_MODULE_KEYS,
    get_homepage_module_settings,
    save_homepage_module_settings,
)
from app.services.import_validation import build_import_validation_report
from app.services.league_rules import (
    evaluate_contract_mutation_allowed,
    evaluate_points_economy_mutations_allowed,
    get_league_rules,
    rule_bool,
    rule_deadline_passed,
    rule_int,
)
from app.services.control_center import build_control_center_snapshot
from app.services.control_center import dry_run_operation_plan
from app.services.control_backups import create_league_backup, list_league_backups, restore_league_backup
from app.services.franchise_health import build_franchise_health_rows
from app.services.admin_alerts import build_admin_alerts_snapshot
from app.services.story_automation import (
    ALLOWED_STORY_CHANNELS,
    dry_run_dispatch_story,
    execute_story_dispatch,
    list_story_schedules,
    schedule_story_publish,
    validate_schedule_datetime,
)
from app.services.discord_events import (
    STAT_LEADER_BOT_COMMAND_KEYS,
    enqueue_discord_event,
    list_heartbeats,
    list_discord_routes,
    list_outbound_events,
    update_discord_routes,
)
from app.services.prediction_center import build_prediction_snapshot
from app.services.awards_tracker import create_voting_cycle, list_cycles, tally_cycle_ballots
from app.services.media_kit import build_media_kit_snapshot
from app.services.member_digest import build_member_watchlist_digest
from app.services.seasons import get_current_season, season_age_reference_date, season_with_imported_data_fallback
from app.services.ap_service import (
    active_redemption_items,
    add_ledger_entry,
    approve_redemption_request,
    new_redemption_token,
    publish_news_and_maybe_award_ap,
    team_ap_balance,
)
from app.services.trade_ai_opinion import fetch_trade_ai_opinion
from app.services.trade_tool import (
    STATUS_COMMISSIONER_DECLINED,
    STATUS_PARTNER_DECLINED,
    STATUS_PENDING_COMMISSIONER,
    STATUS_PENDING_PARTNER,
    STATUS_PUBLISHED,
    format_ledger_summary,
    league_commissioner_user_ids,
    parse_ledger_payload,
    publish_trade_news_articles,
    trade_assets_for_team,
    trade_tool_draft_round_cap,
    validate_ledger,
)
from app.site_models import (
    AdminAuditLog,
    ApRedemptionCatalog,
    ApRedemptionRequest,
    GmInAppNotification,
    GmApprovalRequest,
    GmLeagueMembership,
    GmLeagueMessage,
    GmTradeProposal,
    LeagueDraft,
    LeagueDraftPick,
    LeagueDraftQueueItem,
    LeagueDraftSlot,
    LeagueDraftSoundbite,
    LeagueExpansionDraft,
    LeagueExpansionDraftEligiblePlayer,
    LeagueExpansionDraftPick,
    LeagueExpansionDraftSlot,
    LeagueRuleSetting,
    NewsArticle,
    SiteAnnouncement,
    StoryPublishSchedule,
    AwardsVotingCycle,
    MemberWatchlistItem,
    AdminUndoAction,
    DiscordOutboundEvent,
    User,
)

site_gm_bp = Blueprint("site_gm", __name__)
site_admin_bp = Blueprint("site_admin", __name__, url_prefix="/admin")

_GM_MESSAGE_MAX_LEN = 6000
_APPROVAL_REQUEST_TYPES = ("trade", "signing", "extension")


def _trade_tool_raw_dir() -> Path | None:
    p = Path(str(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)))
    return p if p.is_dir() else None


_TRADE_PLAYER_URL_PLACEHOLDER_ID = 988_776_655


def _finalize_trade_asset_side_urls(side: dict) -> None:
    """Turn ``headshot_rel`` into ``headshot_url`` for JSON (drop internal rel)."""
    for g in ("roster", "unsigned"):
        for it in side.get(g, []):
            if it.get("kind") != "player":
                continue
            rel = it.pop("headshot_rel", None)
            it["headshot_url"] = url_for("static", filename=rel) if rel else None


def _coerce_nonneg_int(v) -> int | None:
    try:
        n = int(v)
    except Exception:
        return None
    return n if n >= 0 else None


def _parse_operation_payload(body: str) -> dict:
    raw = (body or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _team_roster_size(team_id: int) -> int:
    return int(
        db.session.scalar(
            select(func.count(Player.id)).where(
                Player.current_team_id == int(team_id),
                Player.retired.is_(False),
            )
        )
        or 0
    )


def _operation_request_preview(row: GmApprovalRequest, roster_cap: int) -> dict[str, object]:
    preview: dict[str, object] = {
        "details": row.body or "",
        "projection_text": "—",
        "projection_status": "na",
    }
    if row.request_type != "trade":
        return preview
    payload = _parse_operation_payload(row.body or "")
    details = payload.get("details")
    if isinstance(details, str) and details.strip():
        preview["details"] = details.strip()
    inc = _coerce_nonneg_int(payload.get("incoming_count"))
    out = _coerce_nonneg_int(payload.get("outgoing_count"))
    if inc is None or out is None:
        preview["projection_text"] = "Trade payload missing incoming/outgoing counts"
        preview["projection_status"] = "missing"
        return preview
    team_now = _team_roster_size(int(row.team_id))
    team_proj = team_now + inc - out
    status = "ok" if (roster_cap <= 0 or team_proj <= roster_cap) else "over"
    txt = f"Team: {team_now} +{inc} -{out} => {team_proj}/{roster_cap}"
    partner_tid = _coerce_nonneg_int(payload.get("partner_team_id"))
    partner_inc = _coerce_nonneg_int(payload.get("partner_incoming_count"))
    partner_out = _coerce_nonneg_int(payload.get("partner_outgoing_count"))
    if partner_tid and partner_inc is not None and partner_out is not None:
        partner_now = _team_roster_size(partner_tid)
        partner_proj = partner_now + partner_inc - partner_out
        txt += f" | Partner(team_id={partner_tid}): {partner_now} +{partner_inc} -{partner_out} => {partner_proj}/{roster_cap}"
        if roster_cap > 0 and partner_proj > roster_cap:
            status = "over"
    preview["projection_text"] = txt
    preview["projection_status"] = status
    return preview


def _apply_operation_status_change(
    row: GmApprovalRequest,
    *,
    slug: str,
    actor_user_id: int,
    requested_status: str,
    admin_note: str,
) -> dict[str, object]:
    blocked_by_roster_max = False
    blocked_by_trade_deadline = False
    blocked_by_trade_roster = False
    blocked_by_schedule_freeze = False
    blocked_by_waiver_window = False
    trade_projection: dict[str, int] = {}
    roster_cap = rule_int(db.session, slug, "roster_max_size", default=23)
    current_roster_size = _team_roster_size(int(row.team_id))
    effective_status = requested_status
    if (
        effective_status == "approved"
        and row.request_type in {"trade", "signing", "extension"}
        and rule_bool(db.session, slug, "schedule_frozen", default=False)
    ):
        blocked_by_schedule_freeze = True
        effective_status = row.status

    if (
        effective_status == "approved"
        and row.request_type in {"trade", "signing", "extension"}
        and rule_deadline_passed(db.session, slug, "trade_deadline_utc")
    ):
        blocked_by_trade_deadline = True
        effective_status = row.status

    if (
        effective_status == "approved"
        and row.request_type == "signing"
        and not rule_bool(db.session, slug, "waiver_window_open", default=True)
    ):
        blocked_by_waiver_window = True
        effective_status = row.status

    if effective_status == "approved" and row.request_type == "trade":
        if not blocked_by_trade_deadline:
            payload = _parse_operation_payload(row.body)
            inc = _coerce_nonneg_int(payload.get("incoming_count"))
            out = _coerce_nonneg_int(payload.get("outgoing_count"))
            if roster_cap > 0 and inc is not None and out is not None:
                projected = current_roster_size + inc - out
                trade_projection["team_projected_roster_size"] = int(projected)
                if projected > roster_cap:
                    blocked_by_trade_roster = True
                    effective_status = row.status
            partner_tid = _coerce_nonneg_int(payload.get("partner_team_id"))
            partner_inc = _coerce_nonneg_int(payload.get("partner_incoming_count"))
            partner_out = _coerce_nonneg_int(payload.get("partner_outgoing_count"))
            if (
                not blocked_by_trade_roster
                and roster_cap > 0
                and partner_tid
                and partner_inc is not None
                and partner_out is not None
            ):
                partner_roster_size = _team_roster_size(partner_tid)
                partner_projected = partner_roster_size + partner_inc - partner_out
                trade_projection["partner_team_id"] = int(partner_tid)
                trade_projection["partner_projected_roster_size"] = int(partner_projected)
                if partner_projected > roster_cap:
                    blocked_by_trade_roster = True
                    effective_status = row.status

    if (
        effective_status == "approved"
        and row.request_type in {"signing", "extension"}
        and roster_cap > 0
        and current_roster_size >= roster_cap
    ):
        blocked_by_roster_max = True
        effective_status = row.status

    row.status = effective_status
    row.admin_note = admin_note.strip()
    if (
        blocked_by_roster_max
        or blocked_by_trade_deadline
        or blocked_by_trade_roster
        or blocked_by_schedule_freeze
        or blocked_by_waiver_window
    ):
        row.processed_by_user_id = None
        row.processed_at = None
    else:
        row.processed_by_user_id = int(actor_user_id)
        row.processed_at = datetime.utcnow()

    db.session.add(
        AdminAuditLog(
            admin_user_id=int(actor_user_id),
            league_slug=slug,
            action="operations_queue_status",
            detail_json=json.dumps(
                {
                    "request_id": int(row.id),
                    "status": row.status,
                    "request_type": row.request_type,
                    "team_id": int(row.team_id),
                    "roster_max_size": int(roster_cap),
                    "current_roster_size": int(current_roster_size),
                    "blocked_by_roster_max": bool(blocked_by_roster_max),
                    "blocked_by_trade_deadline": bool(blocked_by_trade_deadline),
                    "blocked_by_trade_roster": bool(blocked_by_trade_roster),
                    "blocked_by_schedule_freeze": bool(blocked_by_schedule_freeze),
                    "blocked_by_waiver_window": bool(blocked_by_waiver_window),
                    "trade_projection": trade_projection,
                }
            ),
        )
    )
    return {
        "row_id": int(row.id),
        "effective_status": row.status,
        "requested_status": requested_status,
        "blocked_by_roster_max": bool(blocked_by_roster_max),
        "blocked_by_trade_deadline": bool(blocked_by_trade_deadline),
        "blocked_by_trade_roster": bool(blocked_by_trade_roster),
        "blocked_by_schedule_freeze": bool(blocked_by_schedule_freeze),
        "blocked_by_waiver_window": bool(blocked_by_waiver_window),
        "blocked": bool(
            blocked_by_roster_max
            or blocked_by_trade_deadline
            or blocked_by_trade_roster
            or blocked_by_schedule_freeze
            or blocked_by_waiver_window
        ),
    }


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


def _create_undo_action(
    *,
    league_slug: str,
    action_key: str,
    entity_type: str,
    entity_id: int,
    before: dict,
    after: dict,
    note: str = "",
) -> None:
    db.session.add(
        AdminUndoAction(
            league_slug=league_slug,
            action_key=action_key,
            entity_type=entity_type,
            entity_id=int(entity_id),
            before_json=json.dumps(before or {}),
            after_json=json.dumps(after or {}),
            note=note or "",
            created_by_user_id=int(current_user.id) if getattr(current_user, "is_authenticated", False) else None,
            created_at=datetime.utcnow(),
            is_reverted=False,
        )
    )


def _enqueue_discord_event(event_key: str, payload: dict) -> None:
    slug = _league_slug()
    try:
        enqueue_discord_event(
            db.session,
            league_slug=slug,
            event_key=event_key,
            payload=payload or {},
            created_by_user_id=int(current_user.id) if getattr(current_user, "is_authenticated", False) else None,
        )
    except Exception:
        # Never block primary admin flows on outbound queue writes.
        pass


def _season_rollover_defaults() -> dict[str, object]:
    cur = db.session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if cur is None:
        cur = db.session.scalar(select(Season).order_by(Season.id.desc()).limit(1))
    current_label = str(cur.label) if cur and cur.label else ""
    current_start = int(cur.start_year) if cur and cur.start_year is not None else None
    current_end = int(cur.end_year) if cur and cur.end_year is not None else None
    next_start = (current_start + 1) if current_start is not None else None
    next_end = (current_end + 1) if current_end is not None else None
    next_label = ""
    if next_start is not None and next_end is not None:
        next_label = f"{next_start}-{next_end}"
    elif current_label:
        next_label = f"{current_label} (next)"
    return {
        "current_id": int(cur.id) if cur else None,
        "current_label": current_label,
        "current_start": current_start,
        "current_end": current_end,
        "next_start": next_start,
        "next_end": next_end,
        "next_label": next_label,
    }


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
    if rule_bool(db.session, slug, "schedule_frozen", default=False):
        flash("Redemptions are temporarily closed — schedule is frozen by league rule.", "err")
        return redirect(url_for("site_gm.action_points_page"))
    if not rule_bool(db.session, slug, "waiver_window_open", default=True):
        flash("Redemptions are temporarily closed by league rules (waiver window is closed).", "err")
        return redirect(url_for("site_gm.action_points_page"))
    if rule_deadline_passed(db.session, slug, "trade_deadline_utc"):
        flash("Redemptions are closed after the configured trade deadline.", "err")
        return redirect(url_for("site_gm.action_points_page"))
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


@site_gm_bp.get("/operations/request")
@login_required
def operations_request_redirect():
    """Old Ops Request URL → canonical Trade Tool path (bookmarks, external links)."""
    return redirect(url_for("site_gm.trade_tool"), code=301)


@site_gm_bp.route("/trade-tool", methods=["GET"])
@login_required
def trade_tool():
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    my_team = db.session.get(Team, int(mem.team_id))
    others = list_other_active_gms(slug, current_user.id)
    team_ids = {m.team_id for m, _ in others}
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
            teams_by_id[t.id] = t
    partner_options: list[dict[str, object]] = []
    for m, u in others:
        tm = teams_by_id.get(int(m.team_id))
        partner_options.append(
            {
                "user_id": int(u.id),
                "team_id": int(m.team_id),
                "team_name": tm.full_display_name() if tm else f"Team {m.team_id}",
                "gm_name": gm_display_name(u),
            }
        )
    partner_options.sort(key=lambda r: str(r.get("team_name") or "").lower())
    recent = list(
        db.session.scalars(
            select(GmTradeProposal)
            .where(
                GmTradeProposal.league_slug == slug,
                (GmTradeProposal.from_user_id == int(current_user.id))
                | (GmTradeProposal.to_user_id == int(current_user.id)),
            )
            .order_by(GmTradeProposal.created_at.desc())
            .limit(20)
        ).all()
    )
    my_team_logo_url = team_logo_url_for_team(my_team) if my_team else ""
    draft_round_cap = trade_tool_draft_round_cap(db.session, slug)
    player_page_url_template = url_for("main.player_page", player_id=_TRADE_PLAYER_URL_PLACEHOLDER_ID)
    return render_template(
        "trade_tool.html",
        membership=mem,
        my_team=my_team,
        my_team_logo_url=my_team_logo_url,
        partner_options=partner_options,
        recent_proposals=recent,
        gm_display_name=gm_display_name,
        draft_round_cap=draft_round_cap,
        player_page_url_template=player_page_url_template,
    )


@site_gm_bp.get("/operations/trade-tool/assets")
@login_required
def trade_tool_assets():
    slug = _league_slug()
    mem = _membership()
    if not mem:
        abort(404)
    raw_tid = request.args.get("partner_team_id", type=int)
    if not raw_tid or raw_tid <= 0:
        return jsonify({"error": "partner_team_id required"}), 400
    peer = db.session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.team_id == int(raw_tid),
            GmLeagueMembership.status == "active",
            GmLeagueMembership.user_id != int(current_user.id),
        )
    )
    if not peer:
        return jsonify({"error": "Invalid trading partner team."}), 400
    raw_dir = _trade_tool_raw_dir()
    left = trade_assets_for_team(db.session, int(mem.team_id), raw_dir=raw_dir)
    right = trade_assets_for_team(db.session, int(raw_tid), raw_dir=raw_dir)
    _finalize_trade_asset_side_urls(left)
    _finalize_trade_asset_side_urls(right)
    p_user = db.session.get(User, int(peer.user_id))
    p_team = db.session.get(Team, int(raw_tid))
    draft_cap = trade_tool_draft_round_cap(db.session, slug)
    if (request.args.get("ai") or "").strip() in ("1", "true", "yes"):
        draft_cap = min(8, int(draft_cap))
    player_tpl = url_for("main.player_page", player_id=_TRADE_PLAYER_URL_PLACEHOLDER_ID)
    return jsonify(
        {
            "left_team_id": int(mem.team_id),
            "right_team_id": int(raw_tid),
            "left": left,
            "right": right,
            "draft_round_cap": int(draft_cap),
            "player_page_url_template": player_tpl,
            "partner_team_name": p_team.full_display_name() if p_team else "",
            "partner_gm_name": gm_display_name(p_user),
            "partner_logo_url": team_logo_url_for_team(p_team) if p_team else "",
        }
    )


@site_gm_bp.post("/operations/trade-tool/submit")
@login_required
def trade_tool_submit():
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    partner_team_id = request.form.get("partner_team_id", type=int)
    ledger_raw = (request.form.get("ledger_json") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    if not partner_team_id or partner_team_id <= 0:
        flash("Choose a trading partner team.", "err")
        return redirect(url_for("site_gm.trade_tool"))
    peer_mem = db.session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.team_id == int(partner_team_id),
            GmLeagueMembership.status == "active",
            GmLeagueMembership.user_id != int(current_user.id),
        )
    )
    if not peer_mem:
        flash("That team is not an active GM partner in this league.", "err")
        return redirect(url_for("site_gm.trade_tool"))
    left_out, right_out = parse_ledger_payload(ledger_raw)
    err = validate_ledger(
        db.session,
        int(mem.team_id),
        int(partner_team_id),
        left_out,
        right_out,
        raw_dir=_trade_tool_raw_dir(),
        league_slug=slug,
    )
    if err:
        flash(err, "err")
        return redirect(url_for("site_gm.trade_tool"))
    payload_obj = {"from_left_to_right": left_out, "from_right_to_left": right_out}
    prop = GmTradeProposal(
        league_slug=slug,
        from_user_id=int(current_user.id),
        from_team_id=int(mem.team_id),
        to_user_id=int(peer_mem.user_id),
        to_team_id=int(partner_team_id),
        status=STATUS_PENDING_PARTNER,
        ledger_json=json.dumps(payload_obj),
        notes=notes[:8000],
    )
    db.session.add(prop)
    db.session.flush()
    from_team = db.session.get(Team, int(mem.team_id))
    to_team = db.session.get(Team, int(partner_team_id))
    summary = format_ledger_summary(db.session, from_team, to_team, left_out, right_out)
    peer = db.session.get(User, int(peer_mem.user_id))
    review_path = url_for("site_gm.trade_proposal_detail", pid=int(prop.id))
    msg_body = (
        f"You have a new trade proposal from {gm_display_name(current_user)} "
        f"({from_team.full_display_name() if from_team else 'your partner'}).\n\n"
        f"{summary}\n\n"
        f"Open to approve or decline:\n{review_path}"
    )
    db.session.add(
        GmLeagueMessage(
            league_slug=slug,
            from_user_id=int(current_user.id),
            to_user_id=int(peer_mem.user_id),
            body=msg_body[:_GM_MESSAGE_MAX_LEN],
        )
    )
    notify_trade_proposal_partner(
        slug,
        partner_user_id=int(peer_mem.user_id),
        proposal_id=int(prop.id),
        summary_preview=summary,
    )
    db.session.commit()
    flash("Trade submitted. Your partner was messaged and notified in GM Messages.", "ok")
    return redirect(url_for("site_gm.trade_tool"))


def _ai_trade_draft_round_cap(session, league_slug: str) -> int:
    return min(8, int(trade_tool_draft_round_cap(session, league_slug)))


@site_gm_bp.route("/ai-trade-tool", methods=["GET"])
@login_required
def ai_trade_tool():
    """Hypothetical trade + entertainment AI opinion (not submitted for approval)."""
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    my_team = db.session.get(Team, int(mem.team_id))
    others = list_other_active_gms(slug, current_user.id)
    team_ids = {m.team_id for m, _ in others}
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
            teams_by_id[t.id] = t
    partner_options: list[dict[str, object]] = []
    for m, u in others:
        tm = teams_by_id.get(int(m.team_id))
        partner_options.append(
            {
                "user_id": int(u.id),
                "team_id": int(m.team_id),
                "team_name": tm.full_display_name() if tm else f"Team {m.team_id}",
                "gm_name": gm_display_name(u),
            }
        )
    partner_options.sort(key=lambda r: str(r.get("team_name") or "").lower())
    my_team_logo_url = team_logo_url_for_team(my_team) if my_team else ""
    draft_round_cap = _ai_trade_draft_round_cap(db.session, slug)
    player_page_url_template = url_for("main.player_page", player_id=_TRADE_PLAYER_URL_PLACEHOLDER_ID)
    return render_template(
        "ai_trade_tool.html",
        membership=mem,
        my_team=my_team,
        my_team_logo_url=my_team_logo_url,
        partner_options=partner_options,
        gm_display_name=gm_display_name,
        draft_round_cap=draft_round_cap,
        player_page_url_template=player_page_url_template,
    )


@site_gm_bp.post("/operations/ai-trade-tool/evaluate")
@login_required
def ai_trade_tool_evaluate():
    from flask_wtf.csrf import validate_csrf

    slug = _league_slug()
    mem = _membership()
    if not mem:
        return jsonify({"error": "No active GM membership for this league."}), 403
    data = request.get_json(silent=True) or {}
    try:
        validate_csrf(data.get("csrf_token"))
    except Exception:
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    partner_team_id = data.get("partner_team_id")
    try:
        partner_team_id = int(partner_team_id)
    except (TypeError, ValueError):
        partner_team_id = 0
    if not partner_team_id or partner_team_id <= 0:
        return jsonify({"error": "partner_team_id required"}), 400
    peer_mem = db.session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.team_id == int(partner_team_id),
            GmLeagueMembership.status == "active",
            GmLeagueMembership.user_id != int(current_user.id),
        )
    )
    if not peer_mem:
        return jsonify({"error": "That team is not an active GM partner in this league."}), 400
    ledger_obj = data.get("ledger")
    if not isinstance(ledger_obj, dict):
        return jsonify({"error": "ledger object required"}), 400
    ledger_raw = json.dumps(ledger_obj)
    notes = str(data.get("notes") or "").strip()[:8000]
    left_out, right_out = parse_ledger_payload(ledger_raw)
    cap = _ai_trade_draft_round_cap(db.session, slug)
    err = validate_ledger(
        db.session,
        int(mem.team_id),
        int(partner_team_id),
        left_out,
        right_out,
        raw_dir=_trade_tool_raw_dir(),
        league_slug=slug,
        draft_round_cap=cap,
    )
    if err:
        return jsonify({"error": err}), 400
    from_team = db.session.get(Team, int(mem.team_id))
    to_team = db.session.get(Team, int(partner_team_id))
    out = fetch_trade_ai_opinion(
        db.session,
        user_id=int(current_user.id),
        from_team=from_team,
        to_team=to_team,
        left=left_out,
        right=right_out,
        notes=notes,
    )
    if out.get("error"):
        return jsonify({"error": out["error"], "details": out.get("details") or ""}), 503
    return jsonify(out)


def _normalize_hex_color(raw: str | None) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s.startswith("#"):
        hx = s[1:]
    else:
        hx = s
    if len(hx) == 3:
        hx = "".join(c * 2 for c in hx)
    if len(hx) != 6 or any(c not in "0123456789abcdefABCDEF" for c in hx):
        return None
    return "#" + hx.upper()


def _draft_lottery_team_rows() -> list[dict[str, object]]:
    """Serialize teams for the admin draft lottery UI (BOWL-Fantasy only)."""
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    rows: list[dict[str, object]] = []
    for t in teams:
        rows.append(
            {
                "id": int(t.id),
                "slug": str(t.slug),
                "name": t.full_display_name(),
                "abbr": str(t.abbreviation or "")[:8],
                "logo_url": team_logo_url_for_team(t),
                "primary": _normalize_hex_color(getattr(t, "primary_color", None)),
                "secondary": _normalize_hex_color(getattr(t, "secondary_color", None)),
                "text": _normalize_hex_color(getattr(t, "text_color", None)),
            }
        )
    return rows


@site_gm_bp.route("/draft-lottery", methods=["GET"])
@login_required
def draft_lottery():
    """Weighted 8-slot draft lottery sim (BOWL-Fantasy site admins only)."""
    slug = _league_slug()
    if slug != "bowl-fantasy":
        abort(404)
    if not getattr(current_user, "is_admin", False):
        flash("Draft lottery is only available to league admins.", "err")
        return redirect(url_for("main.home"))
    team_rows = _draft_lottery_team_rows()
    return render_template("draft_lottery.html", team_rows=team_rows)


@site_gm_bp.route("/boost-lottery", methods=["GET"])
@login_required
def boost_lottery():
    """Draft boost ticket lottery (Fantasy / Cap / Historical site admins)."""
    slug = _league_slug()
    if slug not in ("bowl-fantasy", "bowl-cap", "bowl-historical"):
        abort(404)
    if not getattr(current_user, "is_admin", False):
        flash("Boost lottery is only available to league admins.", "err")
        return redirect(url_for("main.home"))
    boost_theme = "fantasy" if slug == "bowl-fantasy" else ("cap" if slug == "bowl-cap" else "historical")
    return render_template("boost_lottery.html", boost_theme=boost_theme)


@site_gm_bp.get("/operations/trade-proposal/<int:pid>")
@login_required
def trade_proposal_detail(pid: int):
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    prop = db.session.get(GmTradeProposal, pid)
    if not prop or prop.league_slug != slug:
        abort(404)
    uid = int(current_user.id)
    is_partner = prop.to_user_id == uid
    is_proposer = prop.from_user_id == uid
    if not is_partner and not is_proposer:
        abort(403)
    from_team = db.session.get(Team, int(prop.from_team_id))
    to_team = db.session.get(Team, int(prop.to_team_id))
    left_out, right_out = parse_ledger_payload(prop.ledger_json)
    summary = format_ledger_summary(db.session, from_team, to_team, left_out, right_out)
    can_partner_act = is_partner and prop.status == STATUS_PENDING_PARTNER
    proposer_u = db.session.get(User, int(prop.from_user_id))
    partner_u = db.session.get(User, int(prop.to_user_id))
    return render_template(
        "trade_proposal_detail.html",
        proposal=prop,
        membership=mem,
        summary=summary,
        from_team=from_team,
        to_team=to_team,
        is_partner=is_partner,
        is_proposer=is_proposer,
        can_partner_act=can_partner_act,
        proposer_display=gm_display_name(proposer_u),
        partner_display=gm_display_name(partner_u),
        gm_display_name=gm_display_name,
    )


@site_gm_bp.post("/operations/trade-proposal/<int:pid>/respond")
@login_required
def trade_proposal_partner_respond(pid: int):
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    prop = db.session.get(GmTradeProposal, pid)
    if not prop or prop.league_slug != slug:
        abort(404)
    if prop.to_user_id != int(current_user.id):
        abort(403)
    if prop.status != STATUS_PENDING_PARTNER:
        flash("This proposal is no longer awaiting your response.", "err")
        return redirect(url_for("site_gm.trade_proposal_detail", pid=pid))
    action = (request.form.get("action") or "").strip().lower()
    from_team = db.session.get(Team, int(prop.from_team_id))
    to_team = db.session.get(Team, int(prop.to_team_id))
    left_out, right_out = parse_ledger_payload(prop.ledger_json)
    summary = format_ledger_summary(db.session, from_team, to_team, left_out, right_out)
    if action == "decline":
        prop.status = STATUS_PARTNER_DECLINED
        prop.partner_acted_at = datetime.utcnow()
        decline_msg = (
            f"Your trade proposal to {to_team.full_display_name() if to_team else 'partner'} "
            f"was declined by {gm_display_name(current_user)}.\n\n{summary}"
        )
        db.session.add(
            GmLeagueMessage(
                league_slug=slug,
                from_user_id=int(current_user.id),
                to_user_id=int(prop.from_user_id),
                body=decline_msg[:_GM_MESSAGE_MAX_LEN],
            )
        )
        notify_trade_outcome_proposer(
            slug,
            proposer_user_id=int(prop.from_user_id),
            proposal_id=int(prop.id),
            title="Trade proposal declined",
            body="Your partner declined the trade.",
        )
        db.session.commit()
        flash("You declined the trade. The proposing GM was notified.", "ok")
        return redirect(url_for("site_gm.trade_tool"))
    if action != "approve":
        flash("Invalid action.", "err")
        return redirect(url_for("site_gm.trade_proposal_detail", pid=pid))
    comm_ids = league_commissioner_user_ids(db.session)
    if not comm_ids:
        flash("No commissioner accounts are configured; contact the league office.", "err")
        return redirect(url_for("site_gm.trade_proposal_detail", pid=pid))
    prop.status = STATUS_PENDING_COMMISSIONER
    prop.partner_acted_at = datetime.utcnow()
    admin_path = url_for("site_admin.admin_trade_proposal_detail", pid=int(prop.id))
    for cid in comm_ids:
        db.session.add(
            GmLeagueMessage(
                league_slug=slug,
                from_user_id=int(current_user.id),
                to_user_id=int(cid),
                body=(
                    f"Trade proposal approved by both GMs; commissioner review needed.\n\n{summary}\n\n"
                    f"Admin: {admin_path}"
                )[:_GM_MESSAGE_MAX_LEN],
            )
        )
    notify_trade_proposal_commissioners(
        slug,
        commissioner_user_ids=comm_ids,
        proposal_id=int(prop.id),
        summary_preview=summary,
    )
    approve_peer_msg = (
        f"{gm_display_name(current_user)} approved your trade proposal "
        f"({from_team.full_display_name() if from_team else ''} / {to_team.full_display_name() if to_team else ''}). "
        f"It is now with the league office for final approval.\n\n{summary}"
    )
    db.session.add(
        GmLeagueMessage(
            league_slug=slug,
            from_user_id=int(current_user.id),
            to_user_id=int(prop.from_user_id),
            body=approve_peer_msg[:_GM_MESSAGE_MAX_LEN],
        )
    )
    db.session.commit()
    flash("You approved the trade. The commissioner was notified.", "ok")
    return redirect(url_for("site_gm.trade_tool"))


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
    if n.kind in ("trade_partner_review", "trade_outcome_proposer", "trade_outcome_partner") and n.article_id:
        return redirect(url_for("site_gm.trade_proposal_detail", pid=int(n.article_id)))
    if n.kind == "trade_commish_review" and n.article_id:
        return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=int(n.article_id)))
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


@site_admin_bp.get("/trade-proposals")
@login_required
def admin_trade_proposals_list():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    rows = list(
        db.session.scalars(
            select(GmTradeProposal)
            .where(GmTradeProposal.league_slug == slug)
            .order_by(GmTradeProposal.created_at.desc())
            .limit(120)
        ).all()
    )
    team_ids = {p.from_team_id for p in rows} | {p.to_team_id for p in rows}
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
            teams_by_id[t.id] = t
    return render_template(
        "admin_trade_proposals.html",
        rows=rows,
        teams_by_id=teams_by_id,
    )


@site_admin_bp.route("/trade-proposals/<int:pid>", methods=["GET", "POST"])
@login_required
def admin_trade_proposal_detail(pid: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    prop = db.session.get(GmTradeProposal, pid)
    if not prop or prop.league_slug != slug:
        abort(404)
    from_team = db.session.get(Team, int(prop.from_team_id))
    to_team = db.session.get(Team, int(prop.to_team_id))
    left_out, right_out = parse_ledger_payload(prop.ledger_json)
    summary = format_ledger_summary(db.session, from_team, to_team, left_out, right_out)
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if prop.status != STATUS_PENDING_COMMISSIONER:
            flash("This proposal is not awaiting commissioner action.", "err")
            return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=pid))
        if action == "approve":
            err = validate_ledger(
                db.session,
                int(prop.from_team_id),
                int(prop.to_team_id),
                left_out,
                right_out,
                raw_dir=_trade_tool_raw_dir(),
                league_slug=slug,
            )
            if err:
                flash(
                    f"Ledger no longer validates against current rosters ({err}). "
                    "Update CSVs or ask GMs to resubmit before approving.",
                    "err",
                )
                return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=pid))
            publish_trade_news_articles(
                db.session,
                league_slug=slug,
                proposal=prop,
                commissioner_user_id=int(current_user.id),
            )
            prop.status = STATUS_PUBLISHED
            prop.commissioner_user_id = int(current_user.id)
            prop.commissioner_acted_at = datetime.utcnow()
            prop.commissioner_note = ""
            ok_body = (
                "The league office approved your trade. Transaction posts appear under "
                "Around the League and on both team pages (roster updates follow imports)."
            )
            notify_trade_outcome_proposer(
                slug,
                proposer_user_id=int(prop.from_user_id),
                proposal_id=int(prop.id),
                title="Trade approved by commissioner",
                body=ok_body,
            )
            notify_trade_outcome_partner(
                slug,
                partner_user_id=int(prop.to_user_id),
                proposal_id=int(prop.id),
                title="Trade approved by commissioner",
                body=ok_body,
            )
            db.session.commit()
            flash("Trade approved and published as league news for both teams.", "ok")
            return redirect(url_for("site_admin.admin_trade_proposals_list"))
        if action == "deny":
            note = (request.form.get("commissioner_note") or "").strip()
            prop.status = STATUS_COMMISSIONER_DECLINED
            prop.commissioner_user_id = int(current_user.id)
            prop.commissioner_acted_at = datetime.utcnow()
            prop.commissioner_note = note[:4000]
            deny_body = (
                "The league office did not approve this trade."
                + (f" Note: {note}" if note else "")
            )
            notify_trade_outcome_proposer(
                slug,
                proposer_user_id=int(prop.from_user_id),
                proposal_id=int(prop.id),
                title="Trade denied by commissioner",
                body=deny_body,
            )
            notify_trade_outcome_partner(
                slug,
                partner_user_id=int(prop.to_user_id),
                proposal_id=int(prop.id),
                title="Trade denied by commissioner",
                body=deny_body,
            )
            db.session.commit()
            flash("Trade proposal denied; both GMs were notified.", "ok")
            return redirect(url_for("site_admin.admin_trade_proposals_list"))
        flash("Unknown action.", "err")
        return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=pid))
    return render_template(
        "admin_trade_proposal_detail.html",
        proposal=prop,
        from_team=from_team,
        to_team=to_team,
        summary=summary,
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


@site_admin_bp.get("/commissioner-sop")
@login_required
def admin_commissioner_sop():
    require_admin()
    return render_template("admin_commissioner_sop.html")


@site_admin_bp.route("/roles", methods=["GET", "POST"])
@login_required
def admin_roles():
    require_admin_role(ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.method == "POST":
        uid_raw = (request.form.get("user_id") or "").strip()
        role_raw = (request.form.get("admin_role") or "").strip().lower()
        is_admin = request.form.get("is_admin") == "1"
        if not uid_raw.isdigit():
            flash("Invalid user.", "err")
            return redirect(url_for("site_admin.admin_roles"))
        uid = int(uid_raw)
        u = db.session.get(User, uid)
        if not u:
            flash("User not found.", "err")
            return redirect(url_for("site_admin.admin_roles"))
        if role_raw and role_raw not in ADMIN_ROLE_VALUES:
            flash("Invalid role value.", "err")
            return redirect(url_for("site_admin.admin_roles"))
        before = {
            "user_id": int(u.id),
            "email": str(u.email or ""),
            "is_admin": bool(u.is_admin),
            "admin_role": (u.admin_role or ""),
        }
        u.is_admin = bool(is_admin)
        u.admin_role = role_raw or None
        after = {
            "user_id": int(u.id),
            "email": str(u.email or ""),
            "is_admin": bool(u.is_admin),
            "admin_role": (u.admin_role or ""),
        }
        if before != after:
            _create_undo_action(
                league_slug=slug,
                action_key="admin_roles_update",
                entity_type="site_user",
                entity_id=int(u.id),
                before={
                    "is_admin": bool(before.get("is_admin")),
                    "admin_role": before.get("admin_role") or "",
                },
                after={
                    "is_admin": bool(after.get("is_admin")),
                    "admin_role": after.get("admin_role") or "",
                },
                note="Admin role / is_admin change",
            )
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="admin_roles_update",
                detail_json=json.dumps({"before": before, "after": after}),
            )
        )
        db.session.commit()
        flash("Admin role updated.", "ok")
        return redirect(url_for("site_admin.admin_roles"))
    users = db.session.scalars(select(User).order_by(User.email.asc())).all()
    role_choices = sorted(ADMIN_ROLE_VALUES)
    return render_template("admin_roles.html", users=users, role_choices=role_choices)


@site_admin_bp.get("/audit")
@login_required
def admin_audit_log():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    action = (request.args.get("action") or "").strip()
    actor_raw = (request.args.get("actor_user_id") or "").strip()
    q = (
        select(AdminAuditLog)
        .where(AdminAuditLog.league_slug == slug)
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
    )
    if action:
        q = q.where(AdminAuditLog.action == action)
    actor_user_id = None
    if actor_raw.isdigit():
        actor_user_id = int(actor_raw)
        q = q.where(AdminAuditLog.admin_user_id == actor_user_id)
    rows = db.session.scalars(q.limit(300)).all()
    actor_ids = sorted({int(r.admin_user_id) for r in rows if r.admin_user_id is not None})
    actors_by_id = {}
    if actor_ids:
        for u in db.session.scalars(select(User).where(User.id.in_(actor_ids))).all():
            actors_by_id[int(u.id)] = u
    action_values = sorted({str(r.action or "") for r in rows if r.action})
    return render_template(
        "admin_audit_log.html",
        rows=rows,
        actors_by_id=actors_by_id,
        action_values=action_values,
        selected_action=action,
        selected_actor_user_id=actor_user_id,
    )


@site_admin_bp.route("/rules", methods=["GET", "POST"])
@login_required
def admin_rules():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.method == "POST":
        rows = get_league_rules(db.session, slug)
        before = {str(r.rule_key): str(r.rule_value or "") for r in rows}
        for r in rows:
            raw = request.form.get(f"rule_{r.rule_key}")
            if raw is None:
                continue
            r.rule_value = str(raw).strip()
            r.updated_by_user_id = int(current_user.id)
            r.updated_at = datetime.utcnow()
        after = {str(r.rule_key): str(r.rule_value or "") for r in rows}
        if before != after:
            _create_undo_action(
                league_slug=slug,
                action_key="league_rules_bulk_update",
                entity_type="league_rules_bulk",
                entity_id=0,
                before={"rules": before},
                after={"rules": after},
                note="League rules form save",
            )
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="league_rules_update",
                detail_json=json.dumps({"before": before, "after": after}),
            )
        )
        db.session.commit()
        flash("League rules updated.", "ok")
        return redirect(url_for("site_admin.admin_rules"))
    rows = get_league_rules(db.session, slug)
    return render_template("admin_rules.html", rows=rows)


@site_admin_bp.get("/control-center")
@login_required
def admin_control_center():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR") or ""))
    snap = build_control_center_snapshot(db.session, raw_dir)
    schedule_frozen = rule_bool(db.session, slug, "schedule_frozen", default=False)
    backup_rows = list_league_backups(slug, limit=20)
    restore_preview = None
    preview_name = (request.args.get("restore_preview") or "").strip()
    if preview_name:
        restore_preview = next((b for b in backup_rows if str(b.get("name")) == preview_name), None)
    restore_verify = (request.args.get("restore_verify") or "").strip() == "1"
    dry_run_result = None
    if request.args.get("dry_run_op"):
        dry_run_result = dry_run_operation_plan(
            repo_root=Path(current_app.root_path).parent,
            league_slug=slug,
            operation=str(request.args.get("dry_run_op") or ""),
        )
    rollover_preview = None
    if (request.args.get("rollover_preview") or "").strip() == "1":
        d = _season_rollover_defaults()
        rollover_preview = {
            "current_label": d.get("current_label") or "—",
            "next_label": (request.args.get("next_label") or str(d.get("next_label") or "")).strip(),
            "next_start": (request.args.get("next_start") or str(d.get("next_start") or "")).strip(),
            "next_end": (request.args.get("next_end") or str(d.get("next_end") or "")).strip(),
            "message": "Dry-run preview only. No changes have been saved.",
        }
    return render_template(
        "admin_control_center.html",
        snapshot=snap,
        league_slug=slug,
        dry_run_result=dry_run_result,
        rollover_preview=rollover_preview,
        rollover_defaults=_season_rollover_defaults(),
        schedule_frozen=schedule_frozen,
        restore_preview=restore_preview,
        execute_result=None,
        backup_rows=backup_rows,
        restore_verify=restore_verify,
    )


@site_admin_bp.post("/control-center/dry-run")
@login_required
def admin_control_center_dry_run():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    op = str(request.form.get("operation") or "").strip().lower()
    result = dry_run_operation_plan(
        repo_root=Path(current_app.root_path).parent,
        league_slug=slug,
        operation=op,
    )
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_dry_run",
            detail_json=json.dumps({"operation": op, "ok": bool(result.get("ok"))}),
        )
    )
    db.session.commit()
    if not result.get("ok"):
        flash("Unknown dry-run operation.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    flash(f"[DRY RUN] Prepared operation preview for '{op}'. No commands executed.", "ok")
    return redirect(url_for("site_admin.admin_control_center", dry_run_op=op))


@site_admin_bp.post("/control-center/execute-refresh")
@login_required
def admin_control_center_execute_refresh():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.form.get("confirm_execute") != "1":
        flash("Execution blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    repo_root = Path(current_app.root_path).parent
    backup = create_league_backup(slug, "refresh_team_aggregates")
    if not backup.get("ok"):
        flash(f"Execution blocked: pre-run backup failed. {backup.get('message')}", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_backup_create",
            detail_json=json.dumps({"reason": "refresh_team_aggregates", "path": backup.get("path", "")}),
        )
    )
    db.session.commit()
    flash(f"Pre-run backup created: {backup.get('path')}", "ok")
    script = repo_root / "scripts" / "refresh_team_aggregates.py"
    started = datetime.utcnow()
    env = dict(os.environ)
    env["LEAGUE_SLUG"] = slug
    if not script.is_file():
        result = {
            "ok": False,
            "exit_code": 127,
            "command": f"{sys.executable} {script}",
            "output": f"Script not found: {script}",
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
    else:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
        )
        output = ((proc.stdout or "").strip() + "\n" + (proc.stderr or "").strip()).strip()
        result = {
            "ok": proc.returncode == 0,
            "exit_code": int(proc.returncode),
            "command": f"{sys.executable} {script}",
            "output": output or "(no output)",
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_execute_refresh",
            detail_json=json.dumps(
                {
                    "ok": bool(result["ok"]),
                    "exit_code": int(result["exit_code"]),
                    "command": result["command"],
                }
            ),
        )
    )
    db.session.commit()
    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR") or ""))
    snap = build_control_center_snapshot(db.session, raw_dir)
    if result["ok"]:
        flash("Refresh completed successfully.", "ok")
    else:
        flash("Refresh failed. Review command output below.", "err")
    return render_template(
        "admin_control_center.html",
        snapshot=snap,
        league_slug=slug,
        dry_run_result=None,
        rollover_preview=None,
        rollover_defaults=_season_rollover_defaults(),
        schedule_frozen=rule_bool(db.session, slug, "schedule_frozen", default=False),
        restore_preview=None,
        execute_result=result,
        backup_rows=list_league_backups(slug, limit=20),
        restore_verify=False,
    )


@site_admin_bp.post("/control-center/execute-import")
@login_required
def admin_control_center_execute_import():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.form.get("confirm_execute") != "1":
        flash("Execution blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    repo_root = Path(current_app.root_path).parent
    backup = create_league_backup(slug, "import_data")
    if not backup.get("ok"):
        flash(f"Execution blocked: pre-run backup failed. {backup.get('message')}", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_backup_create",
            detail_json=json.dumps({"reason": "import_data", "path": backup.get("path", "")}),
        )
    )
    db.session.commit()
    flash(f"Pre-run backup created: {backup.get('path')}", "ok")
    script = repo_root / "scripts" / "import_data.py"
    started = datetime.utcnow()
    env = dict(os.environ)
    env["LEAGUE_SLUG"] = slug
    if not script.is_file():
        result = {
            "ok": False,
            "exit_code": 127,
            "command": f"{sys.executable} {script}",
            "output": f"Script not found: {script}",
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
    else:
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(repo_root),
            env=env,
            capture_output=True,
            text=True,
        )
        output = ((proc.stdout or "").strip() + "\n" + (proc.stderr or "").strip()).strip()
        result = {
            "ok": proc.returncode == 0,
            "exit_code": int(proc.returncode),
            "command": f"{sys.executable} {script}",
            "output": output or "(no output)",
            "started_at": started.isoformat(timespec="seconds"),
            "finished_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_execute_import",
            detail_json=json.dumps(
                {
                    "ok": bool(result["ok"]),
                    "exit_code": int(result["exit_code"]),
                    "command": result["command"],
                }
            ),
        )
    )
    db.session.commit()
    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR") or ""))
    snap = build_control_center_snapshot(db.session, raw_dir)
    if result["ok"]:
        flash("Import completed successfully.", "ok")
    else:
        flash("Import failed. Review command output below.", "err")
    return render_template(
        "admin_control_center.html",
        snapshot=snap,
        league_slug=slug,
        dry_run_result=None,
        rollover_preview=None,
        rollover_defaults=_season_rollover_defaults(),
        schedule_frozen=rule_bool(db.session, slug, "schedule_frozen", default=False),
        restore_preview=None,
        execute_result=result,
        backup_rows=list_league_backups(slug, limit=20),
        restore_verify=False,
    )


@site_admin_bp.post("/control-center/restore-backup")
@login_required
def admin_control_center_restore_backup():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.form.get("confirm_restore") != "1":
        flash("Restore blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    if (request.form.get("confirm_restore_phrase") or "").strip() != "RESTORE":
        flash("Restore blocked: type the exact phrase RESTORE in the confirmation phrase field.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    backup_name = (request.form.get("backup_name") or "").strip()
    if not backup_name:
        flash("Restore blocked: backup selection is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    pre = create_league_backup(slug, "pre_restore")
    if not pre.get("ok"):
        flash(f"Restore blocked: could not create pre-restore backup. {pre.get('message')}", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    restored = restore_league_backup(slug, backup_name)
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_restore_backup",
            detail_json=json.dumps(
                {
                    "requested_backup": backup_name,
                    "ok": bool(restored.get("ok")),
                    "restored_to": restored.get("restored_to", ""),
                    "pre_restore_backup": pre.get("path", ""),
                }
            ),
        )
    )
    if restored.get("ok"):
        _enqueue_discord_event(
            "control_center_restore",
            {
                "backup_name": backup_name,
                "restored_to": restored.get("restored_to", ""),
                "requested_by_user_id": int(current_user.id),
            },
        )
    db.session.commit()
    if restored.get("ok"):
        flash(
            f"Backup restored from {backup_name}. Re-open the Control Center to verify counts; "
            f"you may need to restart the app if SQLite connections were open.",
            "ok",
        )
        return redirect(url_for("site_admin.admin_control_center", restore_verify="1"))
    else:
        flash(f"Restore failed: {restored.get('message')}", "err")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/season-rollover/preview")
@login_required
def admin_control_center_season_rollover_preview():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    next_label = (request.form.get("next_label") or "").strip()
    next_start = (request.form.get("next_start_year") or "").strip()
    next_end = (request.form.get("next_end_year") or "").strip()
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_rollover_preview",
            detail_json=json.dumps(
                {"next_label": next_label, "next_start_year": next_start, "next_end_year": next_end}
            ),
        )
    )
    db.session.commit()
    flash("[DRY RUN] Season rollover preview prepared. No changes saved.", "ok")
    return redirect(
        url_for(
            "site_admin.admin_control_center",
            rollover_preview="1",
            next_label=next_label,
            next_start=next_start,
            next_end=next_end,
        )
    )


@site_admin_bp.post("/control-center/season-rollover/execute")
@login_required
def admin_control_center_season_rollover_execute():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.form.get("confirm_rollover") != "1":
        flash("Season rollover blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    if (request.form.get("confirm_rollover_phrase") or "").strip() != "ROLLOVER":
        flash("Season rollover blocked: type the exact phrase ROLLOVER in the confirmation phrase field.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    next_label = (request.form.get("next_label") or "").strip()
    raw_start = (request.form.get("next_start_year") or "").strip()
    raw_end = (request.form.get("next_end_year") or "").strip()
    try:
        next_start = int(raw_start) if raw_start else None
        next_end = int(raw_end) if raw_end else None
    except Exception:
        flash("Season rollover blocked: start/end year must be valid integers.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    if not next_label:
        flash("Season rollover blocked: next season label is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    if next_start is not None and next_end is not None and next_end < next_start:
        flash("Season rollover blocked: end year cannot be before start year.", "err")
        return redirect(url_for("site_admin.admin_control_center"))

    backup = create_league_backup(slug, "season_rollover")
    if not backup.get("ok"):
        flash(f"Season rollover blocked: pre-run backup failed. {backup.get('message')}", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_backup_create",
            detail_json=json.dumps({"reason": "season_rollover", "path": backup.get("path", "")}),
        )
    )
    db.session.commit()

    current = db.session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if current is None:
        current = db.session.scalar(select(Season).order_by(Season.id.desc()).limit(1))
    target = db.session.scalar(
        select(Season)
        .where(
            Season.label == next_label,
            Season.start_year == next_start,
            Season.end_year == next_end,
        )
        .limit(1)
    )
    if target is None:
        target = Season(
            label=next_label,
            start_year=next_start,
            end_year=next_end,
            is_current=True,
        )
        db.session.add(target)
        db.session.flush()
    all_current = db.session.scalars(select(Season).where(Season.is_current.is_(True))).all()
    for s in all_current:
        s.is_current = False
    target.is_current = True
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_season_rollover_execute",
            detail_json=json.dumps(
                {
                    "from_season_id": int(current.id) if current else None,
                    "from_season_label": str(current.label) if current else "",
                    "to_season_id": int(target.id),
                    "to_season_label": str(target.label),
                    "to_start_year": target.start_year,
                    "to_end_year": target.end_year,
                    "pre_backup_path": backup.get("path", ""),
                }
            ),
        )
    )
    db.session.commit()
    flash(f"Season rollover complete. Current season is now {target.label}.", "ok")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/schedule-freeze-toggle")
@login_required
def admin_control_center_schedule_freeze_toggle():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.form.get("confirm_schedule_toggle") != "1":
        flash("Schedule toggle blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    freeze = (request.form.get("freeze_value") or "").strip().lower() == "1"
    rows = get_league_rules(db.session, slug)
    by_key = {str(r.rule_key): r for r in rows}
    row = by_key.get("schedule_frozen")
    now = datetime.utcnow()
    before = {
        "rule_key": "schedule_frozen",
        "rule_value": str(row.rule_value) if row is not None else "false",
        "updated_by_user_id": int(row.updated_by_user_id) if row and row.updated_by_user_id else None,
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row and row.updated_at else None,
    }
    if row is None:
        row = LeagueRuleSetting(
            league_slug=slug,
            rule_key="schedule_frozen",
            rule_value="true" if freeze else "false",
            updated_by_user_id=int(current_user.id),
            updated_at=now,
        )
    else:
        row.rule_value = "true" if freeze else "false"
        row.updated_by_user_id = int(current_user.id)
        row.updated_at = now
    db.session.add(row)
    if not row.id:
        db.session.flush()
    after = {
        "rule_key": "schedule_frozen",
        "rule_value": str(row.rule_value or "false"),
        "updated_by_user_id": int(row.updated_by_user_id) if row.updated_by_user_id else None,
        "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else None,
    }
    if before != after:
        _create_undo_action(
            league_slug=slug,
            action_key="control_center_schedule_freeze_toggle",
            entity_type="league_rule_setting",
            entity_id=int(row.id),
            before=before,
            after=after,
            note=f"Set schedule_frozen={str(freeze).lower()}",
        )
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_schedule_freeze_toggle",
            detail_json=json.dumps({"schedule_frozen": bool(freeze)}),
        )
    )
    db.session.commit()
    flash(
        "Schedule is now frozen (league scheduling changes should be blocked by consuming flows)."
        if freeze
        else "Schedule is now unfrozen.",
        "ok",
    )
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/create-backup")
@login_required
def admin_control_center_create_backup():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    reason = (request.form.get("reason") or "manual").strip().lower()
    if not reason:
        reason = "manual"
    if request.form.get("confirm_create_backup") != "1":
        flash("Backup creation blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    result = create_league_backup(slug, reason)
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="control_center_backup_create_manual",
            detail_json=json.dumps(
                {"ok": bool(result.get("ok")), "reason": reason, "path": result.get("path", "")}
            ),
        )
    )
    db.session.commit()
    if result.get("ok"):
        flash(f"Backup created: {result.get('name')}", "ok")
    else:
        flash(f"Backup create failed: {result.get('message')}", "err")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/restore-preview")
@login_required
def admin_control_center_restore_preview():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    backup_name = (request.form.get("backup_name") or "").strip()
    if not backup_name:
        flash("Restore preview blocked: backup selection is required.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    return redirect(url_for("site_admin.admin_control_center", restore_preview=backup_name))


@site_admin_bp.get("/operations/queue")
@login_required
def admin_operations_queue():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    queue_view = (request.args.get("view") or "table").strip().lower()
    if queue_view not in {"table", "lane"}:
        queue_view = "table"
    queue_filter = (request.args.get("filter") or "all").strip().lower()
    if queue_filter not in {"all", "pending", "over_cap", "missing_data"}:
        queue_filter = "all"
    queue_sort = (request.args.get("sort") or "newest").strip().lower()
    if queue_sort not in {"newest", "oldest", "over_cap_first"}:
        queue_sort = "newest"
    if queue_view == "lane":
        if request.args.get("filter") is None:
            queue_filter = "pending"
        if request.args.get("sort") is None:
            queue_sort = "over_cap_first"
    dry_run_summary = None
    sticky_selected_ids: list[int] = []
    sticky_bulk_status = ""
    sticky_raw = (request.args.get("dr_ids") or "").strip()
    if sticky_raw:
        vals = []
        for part in sticky_raw.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                v = int(part)
            except Exception:
                continue
            if v > 0:
                vals.append(v)
        sticky_selected_ids = sorted(set(vals))
    sticky_bulk_status = (request.args.get("dr_status") or "").strip().lower()
    if sticky_bulk_status not in {"approved", "denied", "pending"}:
        sticky_bulk_status = ""
    if (request.args.get("dr") or "").strip() == "1":
        def _qp_int(name: str) -> int:
            try:
                return max(0, int((request.args.get(name) or "0").strip()))
            except Exception:
                return 0
        dry_run_summary = {
            "selected": _qp_int("dr_sel"),
            "processable": _qp_int("dr_proc"),
            "blocked": _qp_int("dr_blk"),
            "blocked_deadline": _qp_int("dr_dead"),
            "blocked_roster": _qp_int("dr_ros"),
            "blocked_schedule": _qp_int("dr_sch"),
            "blocked_waiver": _qp_int("dr_wav"),
            "missing": _qp_int("dr_miss"),
            "requested_status": (request.args.get("dr_status") or "").strip().lower(),
        }
    rows = db.session.scalars(
        select(GmApprovalRequest)
        .where(GmApprovalRequest.league_slug == slug)
        .order_by(GmApprovalRequest.created_at.desc(), GmApprovalRequest.id.desc())
        .limit(200)
    ).all()
    team_ids = {int(r.team_id) for r in rows}
    user_ids = {int(r.user_id) for r in rows}
    roster_cap = rule_int(db.session, slug, "roster_max_size", default=23)
    teams_by_id = {int(t.id): t for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all()} if team_ids else {}
    users_by_id = {int(u.id): u for u in db.session.scalars(select(User).where(User.id.in_(user_ids))).all()} if user_ids else {}
    preview_by_id = {int(r.id): _operation_request_preview(r, roster_cap) for r in rows}
    filter_counts = {
        "all": len(rows),
        "pending": sum(1 for r in rows if (r.status or "") == "pending"),
        "over_cap": sum(1 for r in rows if preview_by_id.get(int(r.id), {}).get("projection_status") == "over"),
        "missing_data": sum(1 for r in rows if preview_by_id.get(int(r.id), {}).get("projection_status") == "missing"),
    }
    if queue_filter == "pending":
        rows = [r for r in rows if (r.status or "") == "pending"]
    elif queue_filter == "over_cap":
        rows = [r for r in rows if preview_by_id.get(int(r.id), {}).get("projection_status") == "over"]
    elif queue_filter == "missing_data":
        rows = [r for r in rows if preview_by_id.get(int(r.id), {}).get("projection_status") == "missing"]
    if queue_sort == "oldest":
        rows = sorted(
            rows,
            key=lambda r: (r.created_at or datetime.min, int(r.id or 0)),
        )
    elif queue_sort == "over_cap_first":
        rows = sorted(
            rows,
            key=lambda r: (
                0 if preview_by_id.get(int(r.id), {}).get("projection_status") == "over" else 1,
                -(int(getattr(r, "id", 0) or 0)),
            ),
        )
    else:
        rows = sorted(
            rows,
            key=lambda r: (r.created_at or datetime.min, int(r.id or 0)),
            reverse=True,
        )
    return render_template(
        "admin_operations_queue.html",
        rows=rows,
        teams_by_id=teams_by_id,
        users_by_id=users_by_id,
        preview_by_id=preview_by_id,
        queue_view=queue_view,
        queue_filter=queue_filter,
        queue_sort=queue_sort,
        filter_counts=filter_counts,
        dry_run_summary=dry_run_summary,
        sticky_selected_ids=sticky_selected_ids,
        sticky_bulk_status=sticky_bulk_status,
    )


@site_admin_bp.post("/operations/queue/<int:rid>/status")
@login_required
def admin_operations_queue_set_status(rid: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    row = db.session.get(GmApprovalRequest, rid)
    if not row or row.league_slug != slug:
        abort(404)
    new_status = (request.form.get("status") or "").strip().lower()
    if new_status not in {"approved", "denied", "pending"}:
        flash("Invalid status.", "err")
        return redirect(url_for("site_admin.admin_operations_queue"))
    queue_filter = (request.form.get("queue_filter") or "all").strip().lower()
    if queue_filter not in {"all", "pending", "over_cap", "missing_data"}:
        queue_filter = "all"
    queue_sort = (request.form.get("queue_sort") or "newest").strip().lower()
    if queue_sort not in {"newest", "oldest", "over_cap_first"}:
        queue_sort = "newest"
    queue_view = (request.form.get("queue_view") or "table").strip().lower()
    if queue_view not in {"table", "lane"}:
        queue_view = "table"
    before = {
        "status": str(row.status or ""),
        "admin_note": str(row.admin_note or ""),
        "processed_by_user_id": int(row.processed_by_user_id) if row.processed_by_user_id else None,
        "processed_at": row.processed_at.isoformat(timespec="seconds") if row.processed_at else None,
    }
    result = _apply_operation_status_change(
        row,
        slug=slug,
        actor_user_id=int(current_user.id),
        requested_status=new_status,
        admin_note=(request.form.get("admin_note") or ""),
    )
    after = {
        "status": str(row.status or ""),
        "admin_note": str(row.admin_note or ""),
        "processed_by_user_id": int(row.processed_by_user_id) if row.processed_by_user_id else None,
        "processed_at": row.processed_at.isoformat(timespec="seconds") if row.processed_at else None,
    }
    if before != after:
        _create_undo_action(
            league_slug=slug,
            action_key="operations_queue_status",
            entity_type="gm_approval_request",
            entity_id=int(row.id),
            before=before,
            after=after,
            note=f"Requested status change to {new_status}",
        )
    db.session.commit()
    if result.get("blocked"):
        if result.get("blocked_by_schedule_freeze"):
            flash("Request not changed: schedule is frozen by league rule.", "err")
            return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))
        if result.get("blocked_by_waiver_window"):
            flash("Request not changed: waiver window is closed by league rule.", "err")
            return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))
        if result.get("blocked_by_trade_deadline"):
            flash("Request not changed: trade deadline rule blocked approval.", "err")
            return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))
        flash("Request not changed because a league rule blocked approval.", "err")
    else:
        flash("Request status updated.", "ok")
        _enqueue_discord_event(
            "trade_request",
            {
                "request_id": int(row.id),
                "request_type": str(row.request_type or ""),
                "team_id": int(row.team_id),
                "status": str(row.status or ""),
                "admin_note": str(row.admin_note or "")[:240],
            },
        )
    return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))


@site_admin_bp.post("/operations/queue/bulk-status")
@login_required
def admin_operations_queue_bulk_status():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    queue_filter = (request.form.get("queue_filter") or "all").strip().lower()
    if queue_filter not in {"all", "pending", "over_cap", "missing_data"}:
        queue_filter = "all"
    queue_sort = (request.form.get("queue_sort") or "newest").strip().lower()
    if queue_sort not in {"newest", "oldest", "over_cap_first"}:
        queue_sort = "newest"
    queue_view = (request.form.get("queue_view") or "table").strip().lower()
    if queue_view not in {"table", "lane"}:
        queue_view = "table"
    status = (request.form.get("bulk_status") or "").strip().lower()
    if status not in {"approved", "denied", "pending"}:
        flash("Bulk update failed: invalid status.", "err")
        return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))
    raw_ids = request.form.getlist("request_ids")
    ids: list[int] = []
    for rid in raw_ids:
        try:
            v = int(rid)
        except Exception:
            continue
        if v > 0:
            ids.append(v)
    ids = sorted(set(ids))
    if not ids:
        flash("Bulk update skipped: no requests selected.", "err")
        return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))
    rows = db.session.scalars(
        select(GmApprovalRequest).where(
            GmApprovalRequest.league_slug == slug,
            GmApprovalRequest.id.in_(ids),
        )
    ).all()
    by_id = {int(r.id): r for r in rows}
    admin_note = (request.form.get("bulk_admin_note") or "").strip()
    processed = 0
    blocked = 0
    missing = 0
    blocked_schedule = 0
    blocked_waiver = 0
    for rid in ids:
        row = by_id.get(rid)
        if not row:
            missing += 1
            continue
        before = {
            "status": str(row.status or ""),
            "admin_note": str(row.admin_note or ""),
            "processed_by_user_id": int(row.processed_by_user_id) if row.processed_by_user_id else None,
            "processed_at": row.processed_at.isoformat(timespec="seconds") if row.processed_at else None,
        }
        result = _apply_operation_status_change(
            row,
            slug=slug,
            actor_user_id=int(current_user.id),
            requested_status=status,
            admin_note=admin_note,
        )
        after = {
            "status": str(row.status or ""),
            "admin_note": str(row.admin_note or ""),
            "processed_by_user_id": int(row.processed_by_user_id) if row.processed_by_user_id else None,
            "processed_at": row.processed_at.isoformat(timespec="seconds") if row.processed_at else None,
        }
        if before != after:
            _create_undo_action(
                league_slug=slug,
                action_key="operations_queue_bulk_status",
                entity_type="gm_approval_request",
                entity_id=int(row.id),
                before=before,
                after=after,
                note=f"Bulk requested status {status}",
            )
        processed += 1
        if result.get("blocked"):
            blocked += 1
            if result.get("blocked_by_schedule_freeze"):
                blocked_schedule += 1
            if result.get("blocked_by_waiver_window"):
                blocked_waiver += 1
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="operations_queue_bulk_status",
            detail_json=json.dumps(
                {
                    "requested_status": status,
                    "selected_count": len(ids),
                    "processed_count": processed,
                    "blocked_count": blocked,
                    "blocked_schedule_freeze_count": blocked_schedule,
                    "blocked_waiver_window_count": blocked_waiver,
                    "missing_count": missing,
                }
            ),
        )
    )
    db.session.commit()
    if processed:
        flash(
            f"Bulk update complete: processed={processed}, blocked={blocked}, "
            f"schedule-frozen-blocks={blocked_schedule}, waiver-window-blocks={blocked_waiver}, missing={missing}.",
            "ok" if blocked == 0 else "err",
        )
    else:
        flash("Bulk update did not process any rows.", "err")
    return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))


@site_admin_bp.post("/operations/queue/bulk-dry-run")
@login_required
def admin_operations_queue_bulk_dry_run():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    queue_filter = (request.form.get("queue_filter") or "all").strip().lower()
    if queue_filter not in {"all", "pending", "over_cap", "missing_data"}:
        queue_filter = "all"
    queue_sort = (request.form.get("queue_sort") or "newest").strip().lower()
    if queue_sort not in {"newest", "oldest", "over_cap_first"}:
        queue_sort = "newest"
    queue_view = (request.form.get("queue_view") or "table").strip().lower()
    if queue_view not in {"table", "lane"}:
        queue_view = "table"
    status = (request.form.get("bulk_status") or "").strip().lower()
    if status not in {"approved", "denied", "pending"}:
        flash("Bulk dry-run failed: invalid status.", "err")
        return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))
    raw_ids = request.form.getlist("request_ids")
    ids: list[int] = []
    for rid in raw_ids:
        try:
            v = int(rid)
        except Exception:
            continue
        if v > 0:
            ids.append(v)
    ids = sorted(set(ids))
    if not ids:
        flash("Bulk dry-run skipped: no requests selected.", "err")
        return redirect(url_for("site_admin.admin_operations_queue", view=queue_view, filter=queue_filter, sort=queue_sort))
    rows = db.session.scalars(
        select(GmApprovalRequest).where(
            GmApprovalRequest.league_slug == slug,
            GmApprovalRequest.id.in_(ids),
        )
    ).all()
    by_id = {int(r.id): r for r in rows}
    admin_note = (request.form.get("bulk_admin_note") or "").strip()
    processed = 0
    blocked = 0
    missing = 0
    blocked_deadline = 0
    blocked_roster = 0
    blocked_schedule = 0
    blocked_waiver = 0
    for rid in ids:
        row = by_id.get(rid)
        if not row:
            missing += 1
            continue
        result = _apply_operation_status_change(
            row,
            slug=slug,
            actor_user_id=int(current_user.id),
            requested_status=status,
            admin_note=admin_note,
        )
        processed += 1
        if result.get("blocked"):
            blocked += 1
            if result.get("blocked_by_trade_deadline"):
                blocked_deadline += 1
            if result.get("blocked_by_roster_max") or result.get("blocked_by_trade_roster"):
                blocked_roster += 1
            if result.get("blocked_by_schedule_freeze"):
                blocked_schedule += 1
            if result.get("blocked_by_waiver_window"):
                blocked_waiver += 1
    db.session.rollback()
    flash("Dry-run preview generated. No changes were saved.", "ok")
    ids_csv = ",".join(str(x) for x in ids[:200])
    return redirect(
        url_for(
            "site_admin.admin_operations_queue",
            view=queue_view,
            filter=queue_filter,
            sort=queue_sort,
            dr="1",
            dr_sel=len(ids),
            dr_proc=max(0, processed - blocked),
            dr_blk=blocked,
            dr_dead=blocked_deadline,
            dr_ros=blocked_roster,
            dr_sch=blocked_schedule,
            dr_wav=blocked_waiver,
            dr_miss=missing,
            dr_status=status,
            dr_ids=ids_csv,
        )
    )


@site_admin_bp.get("/franchise-health")
@login_required
def admin_franchise_health():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    rows = build_franchise_health_rows(db.session, slug)
    return render_template("admin_franchise_health.html", rows=rows)


@site_admin_bp.get("/analytics-alerts")
@login_required
def admin_analytics_alerts():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_STATS)
    slug = _league_slug()
    snap = build_admin_alerts_snapshot(db.session, slug)
    return render_template("admin_analytics_alerts.html", snapshot=snap)


@site_admin_bp.route("/story-automation", methods=["GET", "POST"])
@login_required
def admin_story_automation():
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.method == "POST":
        try:
            article_id = int(request.form.get("article_id") or "0")
        except ValueError:
            article_id = 0
        channel = (request.form.get("channel") or "site").strip().lower()
        if channel not in ALLOWED_STORY_CHANNELS:
            channel = "site"
        dt_raw = (request.form.get("scheduled_for_utc") or "").strip()
        dry_run_only = (request.form.get("dry_run_only") or "1").strip() == "1"
        if article_id <= 0:
            flash("Schedule create blocked: valid article is required.", "err")
            return redirect(url_for("site_admin.admin_story_automation"))
        art = db.session.get(NewsArticle, article_id)
        if not art or art.league_slug != slug:
            flash("Schedule create blocked: article not found for this league.", "err")
            return redirect(url_for("site_admin.admin_story_automation"))
        try:
            scheduled_for = datetime.fromisoformat(dt_raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            flash("Schedule create blocked: datetime must be ISO format (UTC).", "err")
            return redirect(url_for("site_admin.admin_story_automation"))
        ok_dt, dt_msg = validate_schedule_datetime(scheduled_for)
        if not ok_dt:
            flash(f"Schedule create blocked: {dt_msg}", "err")
            return redirect(url_for("site_admin.admin_story_automation"))
        row = schedule_story_publish(
            db.session,
            league_slug=slug,
            article_id=article_id,
            channel=channel,
            scheduled_for_utc=scheduled_for,
            dry_run_only=dry_run_only,
            created_by_user_id=int(current_user.id),
        )
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="story_schedule_create",
                detail_json=json.dumps(
                    {
                        "schedule_id": int(row.id),
                        "article_id": int(article_id),
                        "channel": channel,
                        "scheduled_for_utc": scheduled_for.isoformat(timespec="seconds"),
                        "dry_run_only": bool(dry_run_only),
                    }
                ),
            )
        )
        db.session.commit()
        flash("Story publish schedule created.", "ok")
        return redirect(url_for("site_admin.admin_story_automation"))
    rows = list_story_schedules(db.session, league_slug=slug, limit=120)
    article_ids = [int(r.article_id) for r in rows if r.article_id]
    by_article = {}
    if article_ids:
        arts = db.session.scalars(select(NewsArticle).where(NewsArticle.id.in_(article_ids))).all()
        by_article = {int(a.id): a for a in arts}
    pending_articles = db.session.scalars(
        select(NewsArticle)
        .where(NewsArticle.league_slug == slug)
        .order_by(NewsArticle.created_at.desc(), NewsArticle.id.desc())
        .limit(200)
    ).all()
    return render_template(
        "admin_story_automation.html",
        rows=rows,
        article_by_id=by_article,
        pending_articles=pending_articles,
        channels=ALLOWED_STORY_CHANNELS,
        discord_webhook_configured=bool(
            str(current_app.config.get("DISCORD_STORY_WEBHOOK_URL") or "").strip()
        ),
        site_public_base_configured=bool(
            str(current_app.config.get("SITE_PUBLIC_BASE_URL") or "").strip()
        ),
    )


@site_admin_bp.post("/story-automation/<int:sid>/dry-run-dispatch")
@login_required
def admin_story_automation_dry_run_dispatch(sid: int):
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    row = db.session.get(StoryPublishSchedule, sid)
    if not row or row.league_slug != slug:
        abort(404)
    result = dry_run_dispatch_story(db.session, schedule_row=row)
    row.last_result_json = json.dumps(result)
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="story_schedule_dry_run_dispatch",
            detail_json=json.dumps({"schedule_id": int(row.id), "ok": bool(result.get("ok"))}),
        )
    )
    db.session.commit()
    flash("Dry-run dispatch executed (preview only).", "ok" if result.get("ok") else "err")
    return redirect(url_for("site_admin.admin_story_automation"))


@site_admin_bp.post("/story-automation/<int:sid>/cancel")
@login_required
def admin_story_automation_cancel(sid: int):
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    row = db.session.get(StoryPublishSchedule, sid)
    if not row or row.league_slug != slug:
        abort(404)
    if str(row.status or "").strip().lower() == "dispatched":
        flash("Cancel blocked: schedule already dispatched.", "err")
        return redirect(url_for("site_admin.admin_story_automation"))
    row.status = "cancelled"
    row.processed_at = datetime.utcnow()
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="story_schedule_cancel",
            detail_json=json.dumps({"schedule_id": int(row.id)}),
        )
    )
    db.session.commit()
    flash("Schedule cancelled.", "ok")
    return redirect(url_for("site_admin.admin_story_automation"))


@site_admin_bp.post("/story-automation/<int:sid>/live-dispatch")
@login_required
def admin_story_automation_live_dispatch(sid: int):
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    row = db.session.get(StoryPublishSchedule, sid)
    if not row or row.league_slug != slug:
        abort(404)
    if request.form.get("confirm_story_live_dispatch") != "1":
        flash("Live dispatch blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_story_automation"))
    result = execute_story_dispatch(
        db.session,
        schedule_row=row,
        league_slug=slug,
        discord_webhook_url=str(current_app.config.get("DISCORD_STORY_WEBHOOK_URL") or ""),
        site_public_base_url=str(current_app.config.get("SITE_PUBLIC_BASE_URL") or ""),
        league_display_name=league_display_name(slug),
        news_article_ap_points=int(current_app.config.get("NEWS_ARTICLE_AP_POINTS", 3)),
    )
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="story_schedule_live_dispatch",
            detail_json=json.dumps(
                {
                    "schedule_id": int(row.id),
                    "ok": bool(result.get("ok")),
                    "idempotent": bool(result.get("idempotent")),
                    "channel": row.channel,
                }
            ),
        )
    )
    if result.get("ok"):
        _enqueue_discord_event(
            "story_published",
            {
                "schedule_id": int(row.id),
                "article_id": int(row.article_id),
                "channel": str(row.channel or ""),
                "message": str(result.get("message") or "Story dispatched"),
            },
        )
    db.session.commit()
    flash(
        result.get("message") or ("Dispatch complete." if result.get("ok") else "Dispatch failed."),
        "ok" if result.get("ok") else "err",
    )
    return redirect(url_for("site_admin.admin_story_automation"))


@site_admin_bp.post("/story-automation/<int:sid>/retry-live-dispatch")
@login_required
def admin_story_automation_retry_live_dispatch(sid: int):
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    row = db.session.get(StoryPublishSchedule, sid)
    if not row or row.league_slug != slug:
        abort(404)
    if str(row.status or "").strip().lower() != "failed":
        flash("Retry only applies to failed schedules.", "err")
        return redirect(url_for("site_admin.admin_story_automation"))
    if request.form.get("confirm_story_retry_dispatch") != "1":
        flash("Retry blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_story_automation"))
    row.status = "scheduled"
    db.session.flush()
    result = execute_story_dispatch(
        db.session,
        schedule_row=row,
        league_slug=slug,
        discord_webhook_url=str(current_app.config.get("DISCORD_STORY_WEBHOOK_URL") or ""),
        site_public_base_url=str(current_app.config.get("SITE_PUBLIC_BASE_URL") or ""),
        league_display_name=league_display_name(slug),
        news_article_ap_points=int(current_app.config.get("NEWS_ARTICLE_AP_POINTS", 3)),
    )
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="story_schedule_retry_live_dispatch",
            detail_json=json.dumps({"schedule_id": int(row.id), "ok": bool(result.get("ok"))}),
        )
    )
    db.session.commit()
    flash(
        result.get("message") or ("Retry complete." if result.get("ok") else "Retry failed."),
        "ok" if result.get("ok") else "err",
    )
    return redirect(url_for("site_admin.admin_story_automation"))


@site_admin_bp.route("/prediction-center", methods=["GET", "POST"])
@login_required
def admin_prediction_center():
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    if request.method == "POST":
        team_id = (request.form.get("team_id") or "").strip()
        add_wins = (request.form.get("add_wins") or "0").strip()
        add_otl = (request.form.get("add_otl") or "0").strip()
        add_losses = (request.form.get("add_losses") or "0").strip()
        return redirect(
            url_for(
                "site_admin.admin_prediction_center",
                team_id=team_id,
                add_wins=add_wins,
                add_otl=add_otl,
                add_losses=add_losses,
            )
        )
    def _int_arg(name: str, default: int = 0) -> int:
        try:
            return max(0, int((request.args.get(name) or str(default)).strip()))
        except Exception:
            return int(default)
    selected_team_id = _int_arg("team_id", 0) or None
    add_wins = _int_arg("add_wins", 0)
    add_otl = _int_arg("add_otl", 0)
    add_losses = _int_arg("add_losses", 0)
    snap = build_prediction_snapshot(
        db.session,
        selected_team_id=selected_team_id,
        add_wins=add_wins,
        add_otl=add_otl,
        add_losses=add_losses,
    )
    teams = [{"id": int(r["team_id"]), "name": str(r["team_name"])} for r in snap.get("base_rows", [])]
    return render_template("admin_prediction_center.html", snapshot=snap, teams=teams)


@site_admin_bp.get("/franchise-hubs")
@login_required
def admin_franchise_hubs():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_STATS)
    slug = _league_slug()
    teams = db.session.scalars(select(Team).order_by(Team.name.asc(), Team.id.asc())).all()
    current = db.session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if current is None:
        current = db.session.scalar(select(Season).order_by(Season.id.desc()).limit(1))
    standings_by_team: dict[int, TeamStanding] = {}
    if current is not None:
        rows = db.session.scalars(select(TeamStanding).where(TeamStanding.season_id == int(current.id))).all()
        standings_by_team = {int(r.team_id): r for r in rows}
    active_mems = db.session.scalars(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    mem_by_team = {int(m.team_id): m for m in active_mems}
    user_ids = {int(m.user_id) for m in active_mems}
    users_by_id = (
        {int(u.id): u for u in db.session.scalars(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )
    rows = []
    for t in teams:
        m = mem_by_team.get(int(t.id))
        u = users_by_id.get(int(m.user_id)) if m else None
        st = standings_by_team.get(int(t.id))
        pending_ops = int(
            db.session.scalar(
                select(func.count(GmApprovalRequest.id)).where(
                    GmApprovalRequest.league_slug == slug,
                    GmApprovalRequest.team_id == int(t.id),
                    GmApprovalRequest.status == "pending",
                )
            )
            or 0
        )
        rows.append({"team": t, "membership": m, "user": u, "standing": st, "pending_ops": pending_ops})
    return render_template("admin_franchise_hubs.html", rows=rows, season_label=(current.label if current else "—"))


@site_admin_bp.get("/franchise-hubs/<int:team_id>")
@login_required
def admin_franchise_hub_detail(team_id: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_STATS)
    slug = _league_slug()
    team = db.session.get(Team, int(team_id))
    if not team:
        abort(404)
    current = db.session.scalar(select(Season).where(Season.is_current.is_(True)).limit(1))
    if current is None:
        current = db.session.scalar(select(Season).order_by(Season.id.desc()).limit(1))
    standing = None
    if current is not None:
        standing = db.session.scalar(
            select(TeamStanding).where(
                TeamStanding.season_id == int(current.id),
                TeamStanding.team_id == int(team.id),
            ).limit(1)
        )
    membership = db.session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.team_id == int(team.id),
            GmLeagueMembership.status == "active",
        ).limit(1)
    )
    gm_user = db.session.get(User, int(membership.user_id)) if membership else None
    pending_ops = db.session.scalars(
        select(GmApprovalRequest)
        .where(
            GmApprovalRequest.league_slug == slug,
            GmApprovalRequest.team_id == int(team.id),
            GmApprovalRequest.status == "pending",
        )
        .order_by(GmApprovalRequest.created_at.desc(), GmApprovalRequest.id.desc())
        .limit(20)
    ).all()
    recent_news = db.session.scalars(
        select(NewsArticle)
        .where(
            NewsArticle.league_slug == slug,
            NewsArticle.team_id == int(team.id),
        )
        .order_by(NewsArticle.created_at.desc(), NewsArticle.id.desc())
        .limit(10)
    ).all()
    return render_template(
        "admin_franchise_hub_detail.html",
        team=team,
        season_label=(current.label if current else "—"),
        standing=standing,
        membership=membership,
        gm_user=gm_user,
        pending_ops=pending_ops,
        recent_news=recent_news,
    )


@site_admin_bp.route("/awards-tracker", methods=["GET", "POST"])
@login_required
def admin_awards_tracker():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    slug = _league_slug()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "create_cycle":
            season_label = (request.form.get("season_label") or "").strip()
            title = (request.form.get("title") or "").strip()
            if not season_label or not title:
                flash("Create cycle blocked: season label and title are required.", "err")
                return redirect(url_for("site_admin.admin_awards_tracker"))
            row = create_voting_cycle(
                db.session,
                league_slug=slug,
                season_label=season_label,
                title=title,
                created_by_user_id=int(current_user.id),
            )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="awards_cycle_create",
                    detail_json=json.dumps(
                        {"cycle_id": int(row.id), "season_label": season_label, "title": title}
                    ),
                )
            )
            db.session.commit()
            flash("Awards voting cycle created.", "ok")
            return redirect(url_for("site_admin.admin_awards_tracker", cycle_id=row.id))
        if action == "set_status":
            try:
                cycle_id = int(request.form.get("cycle_id") or "0")
            except ValueError:
                cycle_id = 0
            status = (request.form.get("status") or "").strip().lower()
            if status not in {"open", "closed", "archived"} or cycle_id <= 0:
                flash("Update blocked: invalid cycle/status.", "err")
                return redirect(url_for("site_admin.admin_awards_tracker"))
            row = db.session.get(AwardsVotingCycle, cycle_id)
            if not row or row.league_slug != slug:
                abort(404)
            row.status = status
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="awards_cycle_status",
                    detail_json=json.dumps({"cycle_id": int(row.id), "status": status}),
                )
            )
            db.session.commit()
            flash("Cycle status updated.", "ok")
            return redirect(url_for("site_admin.admin_awards_tracker", cycle_id=cycle_id))
    cycles = list_cycles(db.session, league_slug=slug, limit=80)
    selected_cycle_id = (request.args.get("cycle_id") or "").strip()
    try:
        selected_cycle_id_int = int(selected_cycle_id) if selected_cycle_id else 0
    except ValueError:
        selected_cycle_id_int = 0
    selected_cycle = next((c for c in cycles if int(c.id) == selected_cycle_id_int), None)
    tally_rows = tally_cycle_ballots(db.session, league_slug=slug, cycle_id=selected_cycle_id_int) if selected_cycle else []
    return render_template(
        "admin_awards_tracker.html",
        cycles=cycles,
        selected_cycle=selected_cycle,
        tally_rows=tally_rows,
    )


@site_admin_bp.route("/media-kit", methods=["GET", "POST"])
@login_required
def admin_media_kit():
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    team_id = ""
    season_id = ""
    if request.method == "POST":
        team_id = (request.form.get("team_id") or "").strip()
        season_id = (request.form.get("season_id") or "").strip()
        return redirect(url_for("site_admin.admin_media_kit", team_id=team_id, season_id=season_id))
    team_id = (request.args.get("team_id") or "").strip()
    season_id = (request.args.get("season_id") or "").strip()
    teams = db.session.scalars(select(Team).order_by(Team.name.asc(), Team.id.asc())).all()
    seasons = db.session.scalars(select(Season).order_by(Season.id.desc())).all()
    snapshot = None
    if team_id:
        try:
            tid = int(team_id)
        except ValueError:
            tid = 0
        sid = None
        if season_id:
            try:
                sid = int(season_id)
            except ValueError:
                sid = None
        if tid > 0:
            snapshot = build_media_kit_snapshot(db.session, team_id=tid, season_id=sid)
    return render_template(
        "admin_media_kit.html",
        teams=teams,
        seasons=seasons,
        selected_team_id=team_id,
        selected_season_id=season_id,
        snapshot=snapshot,
    )


@site_admin_bp.route("/member-digests", methods=["GET", "POST"])
@login_required
def admin_member_digests():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    slug = _league_slug()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "add_watch":
            try:
                user_id = int(request.form.get("user_id") or "0")
            except ValueError:
                user_id = 0
            target_type = (request.form.get("target_type") or "").strip().lower()
            target_ref = (request.form.get("target_ref") or "").strip()
            note = (request.form.get("note") or "").strip()
            if user_id <= 0 or target_type not in {"player", "team", "article", "gm"} or not target_ref:
                flash("Add watch blocked: invalid user/target fields.", "err")
                return redirect(url_for("site_admin.admin_member_digests"))
            row = MemberWatchlistItem(
                user_id=user_id,
                league_slug=slug,
                target_type=target_type,
                target_ref=target_ref,
                note=note,
                created_at=datetime.utcnow(),
            )
            db.session.add(row)
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="member_watch_add",
                    detail_json=json.dumps(
                        {
                            "user_id": int(user_id),
                            "target_type": target_type,
                            "target_ref": target_ref,
                        }
                    ),
                )
            )
            db.session.commit()
            flash("Watchlist item added.", "ok")
            return redirect(url_for("site_admin.admin_member_digests"))
    digest = build_member_watchlist_digest(db.session, league_slug=slug)
    users = db.session.scalars(
        select(User).order_by(User.discord_name.asc(), User.username.asc(), User.email.asc()).limit(500)
    ).all()
    return render_template("admin_member_digests.html", digest=digest, users=users)


@site_admin_bp.get("/undo-center")
@login_required
def admin_undo_center():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    rows = db.session.scalars(
        select(AdminUndoAction)
        .where(AdminUndoAction.league_slug == slug)
        .order_by(AdminUndoAction.created_at.desc(), AdminUndoAction.id.desc())
        .limit(200)
    ).all()
    return render_template("admin_undo_center.html", rows=rows)


@site_admin_bp.post("/undo-center/<int:undo_id>/apply")
@login_required
def admin_undo_center_apply(undo_id: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    row = db.session.get(AdminUndoAction, undo_id)
    if not row or row.league_slug != slug:
        abort(404)
    if row.is_reverted:
        flash("Undo skipped: action already reverted.", "err")
        return redirect(url_for("site_admin.admin_undo_center"))
    if request.form.get("confirm_undo") != "1":
        flash("Undo blocked: confirmation checkbox is required.", "err")
        return redirect(url_for("site_admin.admin_undo_center"))
    try:
        before = json.loads(row.before_json or "{}")
    except Exception:
        before = {}
    ok = False
    if row.entity_type == "site_announcement":
        ann = db.session.get(SiteAnnouncement, int(row.entity_id))
        if ann and ann.league_slug == slug:
            ann.is_active = bool(before.get("is_active", ann.is_active))
            ok = True
    elif row.entity_type == "gm_approval_request":
        req = db.session.get(GmApprovalRequest, int(row.entity_id))
        if req and req.league_slug == slug:
            req.status = str(before.get("status") or req.status)
            req.admin_note = str(before.get("admin_note") or "")
            rb = before.get("processed_by_user_id")
            req.processed_by_user_id = int(rb) if rb not in (None, "") else None
            rts = before.get("processed_at")
            if isinstance(rts, str) and rts.strip():
                try:
                    req.processed_at = datetime.fromisoformat(rts)
                except Exception:
                    req.processed_at = None
            else:
                req.processed_at = None
            ok = True
    elif row.entity_type == "league_rule_setting":
        rule = db.session.get(LeagueRuleSetting, int(row.entity_id))
        if rule and rule.league_slug == slug:
            rule.rule_value = str(before.get("rule_value") or rule.rule_value)
            rb = before.get("updated_by_user_id")
            rule.updated_by_user_id = int(rb) if rb not in (None, "") else None
            rts = before.get("updated_at")
            if isinstance(rts, str) and rts.strip():
                try:
                    rule.updated_at = datetime.fromisoformat(rts)
                except Exception:
                    rule.updated_at = datetime.utcnow()
            else:
                rule.updated_at = datetime.utcnow()
            ok = True
    elif row.entity_type == "league_rules_bulk":
        rules_before = before.get("rules") if isinstance(before.get("rules"), dict) else {}
        if rules_before:
            all_rows = get_league_rules(db.session, slug)
            now = datetime.utcnow()
            for kr in all_rows:
                if kr.rule_key in rules_before:
                    kr.rule_value = str(rules_before[kr.rule_key])
                    kr.updated_by_user_id = int(current_user.id)
                    kr.updated_at = now
            ok = True
    elif row.entity_type == "homepage_modules_bulk":
        rows_before = before.get("rows")
        if isinstance(rows_before, list) and rows_before:
            save_homepage_module_settings(db.session, slug, rows_before, int(current_user.id))
            ok = True
    elif row.entity_type == "site_user":
        u = db.session.get(User, int(row.entity_id))
        if u:
            u.is_admin = bool(before.get("is_admin"))
            ar = before.get("admin_role")
            u.admin_role = None if ar in (None, "") else str(ar)
            ok = True
    elif row.entity_type == "ap_redemption_catalog":
        cat = db.session.get(ApRedemptionCatalog, int(row.entity_id))
        if cat:
            cat.is_active = bool(before.get("is_active", cat.is_active))
            ok = True
    if not ok:
        flash("Undo failed: target entity missing or unsupported.", "err")
        return redirect(url_for("site_admin.admin_undo_center"))
    row.is_reverted = True
    row.reverted_by_user_id = int(current_user.id)
    row.reverted_at = datetime.utcnow()
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="undo_apply",
            detail_json=json.dumps(
                {"undo_id": int(row.id), "entity_type": row.entity_type, "entity_id": int(row.entity_id)}
            ),
        )
    )
    db.session.commit()
    flash("Undo applied successfully.", "ok")
    return redirect(url_for("site_admin.admin_undo_center"))


@site_admin_bp.route("/discord-integration", methods=["GET", "POST"])
@login_required
def admin_discord_integration():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    slug = _league_slug()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "save_routes":
            rows = []
            for r in list_discord_routes(db.session, slug):
                key = str(r.event_key or "")
                rows.append(
                    {
                        "event_key": key,
                        "channel_key": (request.form.get(f"channel_{key}") or "").strip()[:64],
                        "is_enabled": request.form.get(f"enabled_{key}") == "1",
                    }
                )
            saved = update_discord_routes(db.session, slug, rows, int(current_user.id))
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_routes_update",
                    detail_json=json.dumps({"rows": saved}),
                )
            )
            db.session.commit()
            flash("Discord route settings updated.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "enqueue_test_event":
            event_key = (request.form.get("event_key") or "").strip()
            routes_now = list_discord_routes(db.session, slug)
            allowed = {str(r.event_key) for r in routes_now}
            if event_key not in allowed:
                flash("Test event blocked: invalid event key.", "err")
                return redirect(url_for("site_admin.admin_discord_integration"))
            test_payload = {
                "message": "Manual Discord test event from admin integration page.",
                "event_key": event_key,
                "requested_by_user_id": int(current_user.id),
                "requested_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
            }
            created = enqueue_discord_event(
                db.session,
                league_slug=slug,
                event_key=event_key,
                payload=test_payload,
                created_by_user_id=int(current_user.id),
            )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_test_event_enqueue",
                    detail_json=json.dumps(
                        {
                            "event_key": event_key,
                            "created_event_id": int(created.id) if created else None,
                            "queued": bool(created is not None),
                        }
                    ),
                )
            )
            db.session.commit()
            if created is None:
                flash("Test event skipped: route is disabled or unavailable for that event key.", "err")
            else:
                flash(f"Test event queued for '{event_key}'.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "enqueue_standings":
            payload = {
                "source": "admin_discord_integration",
                "league_slug": slug,
                "requested_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
            }
            created = enqueue_discord_event(
                db.session,
                league_slug=slug,
                event_key="standings_posted",
                payload=payload,
                created_by_user_id=int(current_user.id),
            )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_standings_event_enqueue",
                    detail_json=json.dumps({"queued": bool(created is not None)}),
                )
            )
            db.session.commit()
            if created is None:
                flash("Standings event skipped: route disabled or missing.", "err")
            else:
                flash("Standings update event queued for the bot.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "enqueue_stat_leaders":
            payload = {
                "source": "admin_discord_integration",
                "league_slug": slug,
                "leader_command_keys": list(STAT_LEADER_BOT_COMMAND_KEYS),
                "requested_at_utc": datetime.utcnow().isoformat(timespec="seconds"),
            }
            created = enqueue_discord_event(
                db.session,
                league_slug=slug,
                event_key="statistical_leaders_posted",
                payload=payload,
                created_by_user_id=int(current_user.id),
            )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_stat_leaders_event_enqueue",
                    detail_json=json.dumps({"queued": bool(created is not None)}),
                )
            )
            db.session.commit()
            if created is None:
                flash("Statistical leaders event skipped: route disabled or missing.", "err")
            else:
                flash("Statistical leaders event queued for the bot.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "replay_failed":
            failed = list_outbound_events(db.session, league_slug=slug, status="failed", limit=500)
            replayed = 0
            for row in failed:
                row.status = "pending"
                row.last_error = ""
                row.next_attempt_at = None
                replayed += 1
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_failed_events_replay",
                    detail_json=json.dumps({"replayed": int(replayed)}),
                )
            )
            db.session.commit()
            flash(f"Replayed {replayed} dead-letter event(s).", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
    status = (request.args.get("status") or "").strip().lower()
    routes = list_discord_routes(db.session, slug)
    events = list_outbound_events(db.session, league_slug=slug, status=status, limit=250)
    dead_letters = list_outbound_events(db.session, league_slug=slug, status="failed", limit=50)
    heartbeats = list_heartbeats(db.session, league_slug=slug, limit=10)
    secret_set = bool(str(current_app.config.get("DISCORD_EVENTS_SHARED_SECRET") or "").strip())
    now = datetime.utcnow()
    queue_recent_ok = any(
        e.created_at and (now - e.created_at) <= timedelta(minutes=5) for e in events[:100]
    )
    heartbeat_rows = [
        {
            "bot_name": str(h.bot_name or "discord-bot"),
            "bot_version": str(h.bot_version or ""),
            "guild_id": str(h.guild_id or ""),
            "last_seen_at": h.last_seen_at,
            "is_fresh": bool(h.last_seen_at and (now - h.last_seen_at) <= timedelta(minutes=5)),
            "age_minutes": (
                int((now - h.last_seen_at).total_seconds() // 60)
                if h.last_seen_at
                else None
            ),
        }
        for h in heartbeats
    ]
    heartbeat_rows.sort(
        key=lambda r: (
            0 if not r["is_fresh"] else 1,
            -(r["age_minutes"] if r["age_minutes"] is not None else -1),
            str(r["bot_name"]).casefold(),
        )
    )
    return render_template(
        "admin_discord_integration.html",
        routes=routes,
        events=events,
        dead_letters=dead_letters,
        selected_status=status,
        secret_set=secret_set,
        queue_recent_ok=queue_recent_ok,
        heartbeat_rows=heartbeat_rows,
    )


@site_admin_bp.post("/discord-events/<int:eid>/requeue")
@login_required
def admin_discord_event_requeue(eid: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    slug = _league_slug()
    row = db.session.get(DiscordOutboundEvent, eid)
    if not row or row.league_slug != slug:
        abort(404)
    row.status = "pending"
    row.last_error = ""
    row.next_attempt_at = None
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="discord_event_requeue",
            detail_json=json.dumps({"event_id": int(row.id), "event_key": str(row.event_key or "")}),
        )
    )
    db.session.commit()
    flash("Event requeued.", "ok")
    return redirect(url_for("site_admin.admin_discord_integration"))


@site_admin_bp.post("/discord-events/<int:eid>/cancel")
@login_required
def admin_discord_event_cancel(eid: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    slug = _league_slug()
    row = db.session.get(DiscordOutboundEvent, eid)
    if not row or row.league_slug != slug:
        abort(404)
    row.status = "cancelled"
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="discord_event_cancel",
            detail_json=json.dumps({"event_id": int(row.id), "event_key": str(row.event_key or "")}),
        )
    )
    db.session.commit()
    flash("Event cancelled.", "ok")
    return redirect(url_for("site_admin.admin_discord_integration"))


@site_admin_bp.route("/announcements", methods=["GET", "POST"])
@login_required
def admin_announcements():
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    if request.method == "POST":
        title = (request.form.get("title") or "").strip()[:200]
        body = (request.form.get("body") or "").strip()
        level = (request.form.get("level") or "info").strip().lower()
        if level not in {"info", "warn", "urgent"}:
            level = "info"
        if not body:
            flash("Announcement body is required.", "err")
            return redirect(url_for("site_admin.admin_announcements"))
        ann = SiteAnnouncement(
            league_slug=slug,
            title=title,
            body=body,
            level=level,
            is_active=True,
            created_by_user_id=int(current_user.id),
            created_at=datetime.utcnow(),
        )
        db.session.add(ann)
        db.session.flush()
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="announcement_create",
                detail_json=json.dumps(
                    {"announcement_id": int(ann.id), "title": title, "level": level}
                ),
            )
        )
        _enqueue_discord_event(
            "announcement_posted",
            {
                "announcement_id": int(ann.id),
                "title": str(ann.title or ""),
                "level": str(ann.level or "info"),
                "body_preview": str(ann.body or "")[:280],
            },
        )
        db.session.commit()
        flash("Announcement posted.", "ok")
        return redirect(url_for("site_admin.admin_announcements"))
    rows = db.session.scalars(
        select(SiteAnnouncement)
        .where(SiteAnnouncement.league_slug == slug)
        .order_by(SiteAnnouncement.created_at.desc(), SiteAnnouncement.id.desc())
        .limit(50)
    ).all()
    return render_template("admin_announcements.html", rows=rows)


@site_admin_bp.post("/announcements/<int:aid>/toggle")
@login_required
def admin_announcement_toggle(aid: int):
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    ann = db.session.get(SiteAnnouncement, aid)
    if not ann or ann.league_slug != slug:
        abort(404)
    before = {"is_active": bool(ann.is_active)}
    ann.is_active = not bool(ann.is_active)
    after = {"is_active": bool(ann.is_active)}
    _create_undo_action(
        league_slug=slug,
        action_key="announcement_toggle",
        entity_type="site_announcement",
        entity_id=int(ann.id),
        before=before,
        after=after,
        note=f"Announcement toggle for id={ann.id}",
    )
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="announcement_toggle",
            detail_json=json.dumps(
                {"announcement_id": int(ann.id), "is_active": bool(ann.is_active)}
            ),
        )
    )
    db.session.commit()
    flash("Announcement status updated.", "ok")
    return redirect(url_for("site_admin.admin_announcements"))


@site_admin_bp.route("/import-validation", methods=["GET", "POST"])
@login_required
def admin_import_validation():
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    raw_dir = current_app.config.get("RAW_IMPORT_DIR")
    logos_dir = current_app.config.get("TEAM_LOGOS_DIR")
    report = build_import_validation_report(
        raw_dir=Path(str(raw_dir)),
        team_logos_dir=Path(str(logos_dir)),
        session=db.session,
    )
    if request.method == "POST":
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="import_validation_run",
                detail_json=json.dumps(
                    {
                        "errors": len(report.get("errors") or []),
                        "warnings": len(report.get("warnings") or []),
                        "missing_required": report.get("missing_required") or [],
                    }
                ),
            )
        )
        db.session.commit()
        flash("Import validation report generated.", "ok")
    return render_template("admin_import_validation.html", report=report)


@site_admin_bp.route("/homepage-modules", methods=["GET", "POST"])
@login_required
def admin_homepage_modules():
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    if request.method == "POST":
        before_settings = get_homepage_module_settings(db.session, slug)
        before_rows = [
            {
                "module_key": r.module_key,
                "is_enabled": bool(r.is_enabled),
                "sort_order": int(r.sort_order or 0),
            }
            for r in before_settings
            if r.module_key in ALLOWED_HOMEPAGE_MODULE_KEYS
        ]
        rows = []
        for key in ALLOWED_HOMEPAGE_MODULE_KEYS:
            enabled = request.form.get(f"enabled_{key}") == "1"
            sort_raw = (request.form.get(f"sort_{key}") or "").strip()
            try:
                sort_order = int(sort_raw)
            except (TypeError, ValueError):
                sort_order = 999
            rows.append({"module_key": key, "is_enabled": enabled, "sort_order": sort_order})
        saved = save_homepage_module_settings(
            db.session,
            slug,
            rows,
            updated_by_user_id=int(current_user.id),
        )
        after_rows = [
            {
                "module_key": r["module_key"],
                "is_enabled": bool(r["is_enabled"]),
                "sort_order": int(r["sort_order"]),
            }
            for r in saved
            if r.get("module_key") in ALLOWED_HOMEPAGE_MODULE_KEYS
        ]
        if before_rows != after_rows:
            _create_undo_action(
                league_slug=slug,
                action_key="homepage_modules_update",
                entity_type="homepage_modules_bulk",
                entity_id=0,
                before={"rows": before_rows},
                after={"rows": after_rows},
                note="Homepage module visibility/order",
            )
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="homepage_modules_update",
                detail_json=json.dumps({"rows": saved}),
            )
        )
        db.session.commit()
        flash("Homepage module settings updated.", "ok")
        return redirect(url_for("site_admin.admin_homepage_modules"))
    settings = get_homepage_module_settings(db.session, slug)
    by_key = {r.module_key: r for r in settings}
    ordered = []
    for key in ALLOWED_HOMEPAGE_MODULE_KEYS:
        row = by_key.get(key)
        if row is None:
            continue
        ordered.append(row)
    ordered.sort(key=lambda r: (int(r.sort_order or 0), r.module_key))
    return render_template(
        "admin_homepage_modules.html",
        rows=ordered,
    )


@site_admin_bp.route("/news/compose", methods=["GET", "POST"])
@login_required
def admin_news_compose():
    """Publish a headline immediately as the league office (no moderation, no AP grant)."""
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
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
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
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
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
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
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    dry_run = request.form.get("dry_run") == "1"
    art = db.session.get(NewsArticle, aid)
    if not art or art.league_slug != slug:
        abort(404)
    if art.status != "pending":
        flash("That submission was already processed.", "err")
        return redirect(url_for("site_admin.admin_news_queue"))
    if dry_run:
        flash(
            f"[DRY RUN] Would approve article #{art.id} ('{art.title}') and award AP per configured rules.",
            "ok",
        )
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
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    dry_run = request.form.get("dry_run") == "1"
    art = db.session.get(NewsArticle, aid)
    if not art or art.league_slug != slug:
        abort(404)
    if art.status != "pending":
        flash("That submission was already processed.", "err")
        return redirect(url_for("site_admin.admin_news_queue"))
    if dry_run:
        flash(
            f"[DRY RUN] Would reject article #{art.id} ('{art.title}') and notify the author in GM Messages.",
            "ok",
        )
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    cur_slug = _league_slug()
    dry_run = request.form.get("dry_run") == "1"
    raw = request.form.getlist("team_slug")
    team_slugs = list(dict.fromkeys(s.strip() for s in raw if s and s.strip()))
    if not team_slugs:
        flash("Select at least one team.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    label = league_display_name(cur_slug)
    if not dry_run:
        pe = evaluate_points_economy_mutations_allowed(db.session, cur_slug)
        if not pe.allowed:
            flash(pe.message, "err")
            return redirect(url_for("site_admin.admin_ap_ledger"))
    note = f"EXPORT: +1 AP ({label})"
    added = 0
    matched_slugs: list[str] = []
    for team_slug in team_slugs:
        tid = team_id_for_slug_in_league(
            cur_slug,
            team_slug,
            orm_session=db.session,
            orm_league_slug=cur_slug,
        )
        if tid is None:
            continue
        matched_slugs.append(team_slug)
        if dry_run:
            added += 1
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
    if dry_run:
        sample = ", ".join(matched_slugs[:8]) if matched_slugs else "none"
        if len(matched_slugs) > 8:
            sample += ", …"
        flash(
            f"[DRY RUN] EXPORT would add {added} ledger row(s) (+1 AP) in {label}. Teams: {sample}",
            "ok",
        )
        return redirect(url_for("site_admin.admin_ap_ledger"))
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    cur_slug = _league_slug()
    dry_run = request.form.get("dry_run") == "1"
    if not dry_run:
        pe = evaluate_points_economy_mutations_allowed(db.session, cur_slug)
        if not pe.allowed:
            flash(pe.message, "err")
            return redirect(url_for("site_admin.admin_ap_ledger"))
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
        preview: list[tuple[str, int]] = []
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
            preview.append((team_slug, 1))
            if dry_run:
                entries += 1
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
        if dry_run:
            show = ", ".join([f"{s}: +1" for s, _d in preview[:8]]) if preview else "none"
            if len(preview) > 8:
                show += ", …"
            flash(
                f"[DRY RUN] PREDICTIONS would add {entries} ledger row(s) in {league_name}. {show}",
                "ok",
            )
            return redirect(url_for("site_admin.admin_ap_ledger"))
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
    preview_rows: list[tuple[str, int]] = []
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
        preview_rows.append((team_slug, int(delta)))
        if dry_run:
            entries += 1
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
    if dry_run:
        show = ", ".join([f"{s}: {d:+d}" for s, d in preview_rows[:8]]) if preview_rows else "none"
        if len(preview_rows) > 8:
            show += ", …"
        if entries:
            flash(
                f"[DRY RUN] {label}: would write {entries} ledger row(s) in {league_name}. {show}",
                "ok",
            )
        else:
            flash(f"[DRY RUN] {label}: no non-zero adjustments detected.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    if request.method == "POST":
        dry_run = request.form.get("dry_run") == "1"
        try:
            tid = int(request.form.get("team_id") or "0")
            delta = int(request.form.get("delta") or "0")
        except ValueError:
            flash("Invalid numbers.", "err")
            return redirect(url_for("site_admin.admin_ap_ledger"))
        note = (request.form.get("note") or "").strip()
        if tid and delta:
            team = db.session.get(Team, tid)
            if dry_run:
                team_label = team.full_display_name() if team else f"team_id={tid}"
                flash(
                    f"[DRY RUN] Would add ledger row: {team_label}, delta {delta:+d}, note '{note}'.",
                    "ok",
                )
                return redirect(url_for("site_admin.admin_ap_ledger"))
            pe = evaluate_points_economy_mutations_allowed(db.session, slug)
            if not pe.allowed:
                flash(pe.message, "err")
                return redirect(url_for("site_admin.admin_ap_ledger"))
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    req = db.session.get(ApRedemptionRequest, rid)
    if not req or req.league_slug != slug:
        abort(404)
    return render_template("admin_ap_request_detail.html", req=req)


@site_admin_bp.post("/ap-requests/<int:rid>/approve")
@login_required
def admin_ap_approve(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    req = db.session.get(ApRedemptionRequest, rid)
    if not req or req.league_slug != slug or req.status != "pending":
        abort(404)
    pe = evaluate_points_economy_mutations_allowed(db.session, slug)
    if not pe.allowed:
        flash(pe.message, "err")
        return redirect(url_for("site_admin.admin_ap_requests"))
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
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
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    group = league_group_for_slug(slug)
    row = db.session.get(ApRedemptionCatalog, cid)
    if row and row.league_group == group:
        before = {"is_active": bool(row.is_active)}
        row.is_active = not row.is_active
        after = {"is_active": bool(row.is_active)}
        _create_undo_action(
            league_slug=slug,
            action_key="catalog_item_toggle",
            entity_type="ap_redemption_catalog",
            entity_id=int(row.id),
            before=before,
            after=after,
            note=f"Catalog #{row.id} active toggle",
        )
        db.session.commit()
    return redirect(url_for("site_admin.admin_catalog"))


@site_admin_bp.route("/contract", methods=["GET", "POST"])
@login_required
def admin_contract_edit():
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    if request.method == "POST":
        cr = evaluate_contract_mutation_allowed(db.session, slug)
        if not cr.allowed:
            flash(cr.message, "err")
            return redirect(url_for("site_admin.admin_contract_edit"))
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
        if salary < 0:
            flash("Salary cannot be negative.", "err")
            return redirect(url_for("site_admin.admin_contract_edit"))
        if rule_bool(db.session, slug, "salary_cap_enabled", default=False):
            cap_amt = rule_int(db.session, slug, "salary_cap_amount", default=0)
            if cap_amt > 0 and pl.current_team_id:
                others_sum = (
                    db.session.execute(
                        select(func.coalesce(func.sum(PlayerContract.average_salary), 0))
                        .join(Player, PlayerContract.player_id == Player.id)
                        .where(
                            Player.current_team_id == int(pl.current_team_id),
                            PlayerContract.player_id != int(pid),
                        )
                    ).scalar_one()
                    or 0
                )
                projected = int(others_sum) + int(salary)
                if projected > int(cap_amt):
                    flash(
                        f"Blocked by salary cap rule: projected team total ${projected:,} exceeds cap ${cap_amt:,}.",
                        "err",
                    )
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


# --- Draft Hub (league-run drafts; site DB) ---


def _parse_scheduled_start(raw: str) -> datetime | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _parse_draft_deadline_date(raw: str, fallback_month: int, fallback_day: int) -> tuple[int, int]:
    """Return month/day from a YYYY-MM-DD admin date input, preserving existing values on bad input."""
    raw = (raw or "").strip()
    if not raw:
        return int(fallback_month), int(fallback_day)
    try:
        parsed = datetime.strptime(raw[:10], "%Y-%m-%d")
    except ValueError:
        return int(fallback_month), int(fallback_day)
    return int(parsed.month), int(parsed.day)


def _purge_draft_soundbite_dir(slug: str, draft_id: int) -> None:
    """Best-effort removal of soundbite files for a deleted draft."""
    import shutil

    folder = Path(current_app.instance_path) / "draft_soundbites" / slug / str(draft_id)
    if folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


@site_admin_bp.route("/draft-hub", methods=["GET", "POST"])
@login_required
def admin_draft_hub():
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    if request.method == "POST" and request.form.get("action") == "delete":
        draft_id_raw = (request.form.get("draft_id") or "").strip()
        if not draft_id_raw.isdigit():
            flash("Invalid draft id.", "err")
            return redirect(url_for("site_admin.admin_draft_hub"))
        target = db.session.get(LeagueDraft, int(draft_id_raw))
        if not target or target.league_slug != slug:
            flash("Draft not found for this site.", "err")
            return redirect(url_for("site_admin.admin_draft_hub"))
        if target.status == "live":
            flash(
                "This draft is live. Complete it (or undo its picks then delete its slots) before deleting.",
                "err",
            )
            return redirect(url_for("site_admin.admin_draft_hub"))
        deleted_name = target.name
        deleted_status = target.status
        db.session.execute(
            delete(LeagueDraftQueueItem).where(LeagueDraftQueueItem.league_draft_id == target.id)
        )
        db.session.execute(
            delete(LeagueDraftPick).where(LeagueDraftPick.league_draft_id == target.id)
        )
        db.session.execute(
            delete(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == target.id)
        )
        db.session.execute(
            delete(LeagueDraftSoundbite).where(LeagueDraftSoundbite.league_draft_id == target.id)
        )
        db.session.delete(target)
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="draft_hub_delete",
                detail_json=json.dumps(
                    {"draft_id": int(draft_id_raw), "name": deleted_name, "status": deleted_status}
                ),
            )
        )
        db.session.commit()
        _purge_draft_soundbite_dir(slug, int(draft_id_raw))
        flash(f"Deleted draft “{deleted_name}”.", "ok")
        return redirect(url_for("site_admin.admin_draft_hub"))
    if request.method == "POST" and request.form.get("action") == "new":
        from app.services.draft_hub_eligibility import default_eligibility_for_league
        from app.services.seasons import get_current_season

        season = get_current_season()
        ty = int(season.start_year) if season and season.start_year else datetime.utcnow().year
        ddef = default_eligibility_for_league(slug)
        row = LeagueDraft(
            league_slug=slug,
            name=(request.form.get("name") or "Draft").strip()[:200] or "Draft",
            status="setup",
            rounds=max(1, int(request.form.get("rounds") or 1)),
            picks_per_round=max(1, int(request.form.get("picks_per_round") or 27)),
            timer_seconds=int(request.form.get("timer_seconds") or 120) or 120,
            empty_queue_timer_seconds=int(request.form.get("empty_queue_timer_seconds") or 120) or 120,
            min_age_years=ddef.min_age_years,
            min_anchor_month=ddef.min_anchor_month,
            min_anchor_day=ddef.min_anchor_day,
            max_age_years=ddef.max_age_years,
            max_anchor_month=ddef.max_anchor_month,
            max_anchor_day=ddef.max_anchor_day,
            timeline_year=ty,
        )
        db.session.add(row)
        db.session.commit()
        flash("Draft created.", "ok")
        return redirect(url_for("site_admin.admin_draft_hub_edit", draft_id=row.id))
    rows = list(
        db.session.scalars(select(LeagueDraft).where(LeagueDraft.league_slug == slug).order_by(LeagueDraft.id.desc())).all()
    )
    return render_template("admin_draft_hub.html", league_slug=slug, drafts=rows)


@site_admin_bp.route("/draft-hub/<int:draft_id>", methods=["GET", "POST"])
@login_required
def admin_draft_hub_edit(draft_id: int):
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    row = db.session.get(LeagueDraft, draft_id)
    if not row or row.league_slug != slug:
        abort(404)
    if request.method == "POST":
        act = (request.form.get("action") or "").strip()
        if act == "save_settings" and row.status == "setup":
            row.name = (request.form.get("name") or row.name).strip()[:200]
            row.rounds = max(1, int(request.form.get("rounds") or row.rounds))
            row.picks_per_round = max(1, int(request.form.get("picks_per_round") or row.picks_per_round))
            row.timer_seconds = max(5, int(request.form.get("timer_seconds") or row.timer_seconds))
            row.empty_queue_timer_seconds = max(
                5, int(request.form.get("empty_queue_timer_seconds") or row.empty_queue_timer_seconds)
            )
            row.timeline_year = int(request.form.get("timeline_year") or row.timeline_year)
            row.min_age_years = int(request.form.get("min_age_years") or row.min_age_years)
            row.max_age_years = int(request.form.get("max_age_years") or row.max_age_years)
            row.min_anchor_month, row.min_anchor_day = _parse_draft_deadline_date(
                request.form.get("min_deadline_date") or "",
                row.min_anchor_month,
                row.min_anchor_day,
            )
            row.max_anchor_month, row.max_anchor_day = _parse_draft_deadline_date(
                request.form.get("max_deadline_date") or "",
                row.max_anchor_month,
                row.max_anchor_day,
            )
            row.scheduled_start_at = _parse_scheduled_start(request.form.get("scheduled_start_at") or "")
            db.session.commit()
            flash("Settings saved.", "ok")
        elif act == "go_live" and row.status == "setup":
            from app.services.draft_hub_state import go_live

            err = go_live(db.session, row, int(current_user.id))
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_go_live",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Draft is now live.", "ok")
            db.session.commit()
        elif act == "undo_pick" and row.status == "live":
            from app.services.draft_hub_state import undo_last_pick

            err = undo_last_pick(db.session, row)
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_undo_pick",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Last pick removed.", "ok")
            db.session.commit()
        elif act == "pause_timer" and row.status == "live":
            from app.services.draft_hub_state import pause_draft_timer

            err = pause_draft_timer(db.session, row)
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_pause_timer",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Draft clock paused.", "ok")
            db.session.commit()
        elif act == "resume_timer" and row.status == "live":
            from app.services.draft_hub_state import resume_draft_timer

            err = resume_draft_timer(db.session, row)
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_resume_timer",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Draft clock resumed.", "ok")
            db.session.commit()
        elif act == "admin_pick" and row.status == "live":
            from app.services.draft_hub_state import resolve_admin_pick

            pid_raw = (request.form.get("player_id") or "").strip()
            if not pid_raw.isdigit():
                flash("Invalid player id.", "err")
            else:
                err = resolve_admin_pick(db.session, row, int(pid_raw), int(current_user.id))
                if err:
                    flash(err, "err")
                else:
                    db.session.add(
                        AdminAuditLog(
                            admin_user_id=int(current_user.id),
                            league_slug=slug,
                            action="draft_hub_admin_pick",
                            detail_json=json.dumps({"draft_id": row.id, "player_id": int(pid_raw)}),
                        )
                    )
                    flash("Pick recorded.", "ok")
            db.session.commit()
        elif act == "save_boosts":
            gold_raw = (request.form.get("gold_picks") or "").strip()
            silver_raw = (request.form.get("silver_picks") or "").strip()

            def _parse_overall_csv(raw: str) -> set[int]:
                out: set[int] = set()
                for token in raw.replace(";", ",").replace("\n", ",").split(","):
                    t = token.strip()
                    if t.isdigit():
                        out.add(int(t))
                return out

            gold = _parse_overall_csv(gold_raw)
            silver = _parse_overall_csv(silver_raw)
            overlap = gold & silver
            if overlap:
                flash(
                    "Pick(s) listed as both gold and silver: "
                    + ", ".join(str(n) for n in sorted(overlap))
                    + ". A slot can only have one tier.",
                    "err",
                )
            else:
                slot_rows = list(
                    db.session.scalars(
                        select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id)
                    ).all()
                )
                slot_by_overall = {int(s.overall_pick): s for s in slot_rows}
                unknown = sorted(
                    n for n in (gold | silver) if n not in slot_by_overall
                )
                applied_gold = 0
                applied_silver = 0
                for s in slot_rows:
                    ov = int(s.overall_pick)
                    if ov in gold:
                        s.boost_tier = "gold"
                        applied_gold += 1
                    elif ov in silver:
                        s.boost_tier = "silver"
                        applied_silver += 1
                    else:
                        s.boost_tier = ""
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_save_boosts",
                        detail_json=json.dumps(
                            {
                                "draft_id": row.id,
                                "gold": sorted(gold),
                                "silver": sorted(silver),
                            }
                        ),
                    )
                )
                db.session.commit()
                msg = (
                    f"Boost picks saved — Gold: {applied_gold}, Silver: {applied_silver}."
                )
                if unknown:
                    msg += (
                        " Ignored (no matching slot): "
                        + ", ".join(str(n) for n in unknown)
                        + "."
                    )
                flash(msg, "ok")
        elif act == "save_generated_slots" and row.status == "setup":
            old_tiers = {
                int(s.overall_pick): s.boost_tier or ""
                for s in db.session.scalars(
                    select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id)
                ).all()
            }
            valid_team_ids = {
                int(tid) for tid in db.session.scalars(select(Team.id)).all()
            }
            db.session.execute(delete(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id))
            total = int(row.rounds) * int(row.picks_per_round)
            created = 0
            traded_count = 0
            for overall in range(1, total + 1):
                tid_raw = (request.form.get(f"slot_team_{overall}") or "").strip()
                if not tid_raw.isdigit():
                    continue
                tid = int(tid_raw)
                orig_raw = (request.form.get(f"slot_orig_{overall}") or "").strip()
                orig_tid = tid
                if orig_raw.isdigit():
                    candidate = int(orig_raw)
                    if candidate in valid_team_ids and candidate != tid:
                        orig_tid = candidate
                        traded_count += 1
                round_no = ((overall - 1) // int(row.picks_per_round)) + 1
                db.session.add(
                    LeagueDraftSlot(
                        league_draft_id=row.id,
                        overall_pick=overall,
                        round=round_no,
                        original_team_id=orig_tid,
                        team_id=tid,
                        boost_tier=old_tiers.get(overall, ""),
                    )
                )
                created += 1
            db.session.commit()
            msg = f"Draft order saved from round builder ({created} slots)."
            if traded_count:
                msg += f" {traded_count} pick(s) tagged as received from a prior trade."
            flash(msg, "ok")
        elif act == "save_slot_teams" and row.status in ("setup", "live"):
            picked_overalls = {
                int(x)
                for x in db.session.scalars(
                    select(LeagueDraftPick.overall_pick).where(LeagueDraftPick.league_draft_id == row.id)
                ).all()
            }
            changed = 0
            skipped = 0
            slots_for_update = list(
                db.session.scalars(
                    select(LeagueDraftSlot)
                    .where(LeagueDraftSlot.league_draft_id == row.id)
                    .order_by(LeagueDraftSlot.overall_pick)
                ).all()
            )
            for slot in slots_for_update:
                overall = int(slot.overall_pick)
                tid_raw = (request.form.get(f"slot_team_{overall}") or "").strip()
                if not tid_raw.isdigit():
                    continue
                if overall in picked_overalls:
                    skipped += 1
                    continue
                new_tid = int(tid_raw)
                if slot.original_team_id is None:
                    slot.original_team_id = int(slot.team_id)
                if new_tid != int(slot.team_id):
                    slot.team_id = new_tid
                    changed += 1
            if changed:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_change_pick_teams",
                        detail_json=json.dumps({"draft_id": row.id, "changed": changed}),
                    )
                )
            db.session.commit()
            msg = f"Pick ownership saved ({changed} changed)."
            if skipped:
                msg += f" {skipped} completed pick(s) were left unchanged."
            flash(msg, "ok")
        elif act == "save_slots" and row.status == "setup":
            raw = (request.form.get("slots_csv") or "").strip()
            lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
            db.session.execute(delete(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id))
            for ln in lines:
                parts = [p.strip() for p in ln.split(",")]
                if len(parts) < 3:
                    continue
                ov, rnd, tid = parts[0], parts[1], parts[2]
                if not ov.isdigit() or not rnd.isdigit() or not tid.isdigit():
                    continue
                notes = parts[3] if len(parts) > 3 else None
                ff = parts[4].strip().lower() in ("1", "true", "yes", "forfeit") if len(parts) > 4 else False
                db.session.add(
                    LeagueDraftSlot(
                        league_draft_id=row.id,
                        overall_pick=int(ov),
                        round=int(rnd),
                        original_team_id=int(tid),
                        team_id=int(tid),
                        forfeited=ff,
                        notes=notes[:500] if notes else None,
                    )
                )
            db.session.commit()
            flash("Draft order saved.", "ok")
        elif act == "upload_sound" and request.files.get("sound_file"):
            f = request.files["sound_file"]
            if not f.filename:
                flash("No file.", "err")
            else:
                ext = Path(f.filename).suffix.lower()
                if ext not in (".mp3", ".wav", ".ogg", ".webm"):
                    flash("Allowed: mp3, wav, ogg, webm.", "err")
                else:
                    mime = f.mimetype or "audio/mpeg"
                    cl = getattr(f, "content_length", None)
                    if cl and int(cl) > 3 * 1024 * 1024:
                        flash("File too large (max 3MB).", "err")
                    else:
                        dest_dir = Path(current_app.instance_path) / "draft_soundbites" / slug / str(row.id)
                        dest_dir.mkdir(parents=True, exist_ok=True)
                        fname = secrets.token_hex(12) + ext
                        out = dest_dir / fname
                        f.save(str(out))
                        label = (request.form.get("sound_label") or Path(f.filename).stem).strip()[:120] or "Sound"
                        db.session.add(
                            LeagueDraftSoundbite(
                                league_draft_id=row.id,
                                display_name=label,
                                stored_filename=fname,
                                mime_type=mime[:80],
                            )
                        )
                        flash("Soundbite added.", "ok")
            db.session.commit()
        return redirect(url_for("site_admin.admin_draft_hub_edit", draft_id=draft_id))

    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    slots = list(
        db.session.scalars(
            select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id).order_by(LeagueDraftSlot.overall_pick)
        ).all()
    )
    sounds = list(
        db.session.scalars(select(LeagueDraftSoundbite).where(LeagueDraftSoundbite.league_draft_id == row.id)).all()
    )
    slots_csv = "\n".join(
        f"{s.overall_pick},{s.round},{s.team_id},{s.notes or ''},{'forfeit' if s.forfeited else ''}".rstrip(",")
        for s in slots
    )
    picked_overalls = {
        int(x)
        for x in db.session.scalars(
            select(LeagueDraftPick.overall_pick).where(LeagueDraftPick.league_draft_id == row.id)
        ).all()
    }
    slots_by_overall = {int(s.overall_pick): s for s in slots}
    max_slot_round = max((int(s.round) for s in slots), default=0)
    total_rounds = max(int(row.rounds), max_slot_round, 1)
    round_slot_rows = []
    for round_no in range(1, total_rounds + 1):
        round_rows = []
        for pick_no in range(1, int(row.picks_per_round) + 1):
            overall = ((round_no - 1) * int(row.picks_per_round)) + pick_no
            slot = slots_by_overall.get(overall)
            round_rows.append(
                {
                    "overall": overall,
                    "round": round_no,
                    "team_id": int(slot.team_id) if slot else None,
                    "original_team_id": int(slot.original_team_id or slot.team_id) if slot else None,
                    "boost_tier": slot.boost_tier if slot else "",
                    "picked": overall in picked_overalls,
                }
            )
        round_slot_rows.append(round_rows)
    wishlist_guidance = []
    if row.status == "live" and row.current_slot_index < len(slots):
        current_slot = slots[row.current_slot_index]
        if current_slot and not current_slot.forfeited:
            picked_player_ids = {
                int(x)
                for x in db.session.scalars(
                    select(LeagueDraftPick.player_id).where(LeagueDraftPick.league_draft_id == row.id)
                ).all()
            }
            memberships = list(
                db.session.scalars(
                    select(GmLeagueMembership)
                    .where(
                        GmLeagueMembership.league_slug == slug,
                        GmLeagueMembership.team_id == int(current_slot.team_id),
                        GmLeagueMembership.status == "active",
                    )
                    .order_by(GmLeagueMembership.user_id.asc())
                ).all()
            )
            for mem in memberships:
                user = db.session.get(User, int(mem.user_id))
                qitems = list(
                    db.session.scalars(
                        select(LeagueDraftQueueItem)
                        .where(
                            LeagueDraftQueueItem.league_draft_id == row.id,
                            LeagueDraftQueueItem.user_id == int(mem.user_id),
                        )
                        .order_by(LeagueDraftQueueItem.sort_order.asc(), LeagueDraftQueueItem.id.asc())
                    ).all()
                )
                top_item = None
                for qi in qitems:
                    if int(qi.player_id) not in picked_player_ids:
                        top_item = qi
                        break
                player = db.session.get(Player, int(top_item.player_id)) if top_item else None
                wishlist_guidance.append(
                    {
                        "gm_name": (
                            (user.username or user.discord_name or user.email)
                            if user
                            else f"User #{mem.user_id}"
                        ),
                        "player_id": int(top_item.player_id) if top_item else None,
                        "player_name": player.full_name if player else "",
                        "queue_count": len(qitems),
                    }
                )
    gold_csv = ", ".join(str(s.overall_pick) for s in slots if s.boost_tier == "gold")
    silver_csv = ", ".join(str(s.overall_pick) for s in slots if s.boost_tier == "silver")
    sched = ""
    if row.scheduled_start_at:
        sched = row.scheduled_start_at.strftime("%Y-%m-%dT%H:%M")
    min_deadline_value = f"{int(row.timeline_year):04d}-{int(row.min_anchor_month):02d}-{int(row.min_anchor_day):02d}"
    max_deadline_value = f"{int(row.timeline_year):04d}-{int(row.max_anchor_month):02d}-{int(row.max_anchor_day):02d}"
    year_min_date = f"{int(row.timeline_year):04d}-01-01"
    year_max_date = f"{int(row.timeline_year):04d}-12-31"
    return render_template(
        "admin_draft_hub_edit.html",
        league_slug=slug,
        draft=row,
        teams=teams,
        slots_csv=slots_csv,
        round_slot_rows=round_slot_rows,
        wishlist_guidance=wishlist_guidance,
        gold_csv=gold_csv,
        silver_csv=silver_csv,
        sounds=sounds,
        sched_value=sched,
        min_deadline_value=min_deadline_value,
        max_deadline_value=max_deadline_value,
        year_min_date=year_min_date,
        year_max_date=year_max_date,
        age_options=list(range(15, 31)),
    )


@site_admin_bp.route("/expansion-draft-hub", methods=["GET", "POST"])
@login_required
def admin_expansion_draft_hub():
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    if request.method == "POST" and request.form.get("action") == "delete":
        draft_id_raw = (request.form.get("draft_id") or "").strip()
        if not draft_id_raw.isdigit():
            flash("Invalid draft id.", "err")
            return redirect(url_for("site_admin.admin_expansion_draft_hub"))
        target = db.session.get(LeagueExpansionDraft, int(draft_id_raw))
        if not target or target.league_slug != slug:
            flash("Expansion draft not found for this site.", "err")
            return redirect(url_for("site_admin.admin_expansion_draft_hub"))
        did = int(target.id)
        name = target.name
        prev_status = target.status
        db.session.execute(
            delete(LeagueExpansionDraftEligiblePlayer).where(
                LeagueExpansionDraftEligiblePlayer.league_expansion_draft_id == did
            )
        )
        db.session.execute(
            delete(LeagueExpansionDraftPick).where(LeagueExpansionDraftPick.league_expansion_draft_id == did)
        )
        db.session.execute(
            delete(LeagueExpansionDraftSlot).where(LeagueExpansionDraftSlot.league_expansion_draft_id == did)
        )
        db.session.delete(target)
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="expansion_draft_hub_delete",
                detail_json=json.dumps(
                    {"draft_id": did, "name": name, "status_before": prev_status}
                ),
            )
        )
        db.session.commit()
        flash(f"Deleted expansion draft “{name}”.", "ok")
        return redirect(url_for("site_admin.admin_expansion_draft_hub"))
    if request.method == "POST" and request.form.get("action") == "new":
        row = LeagueExpansionDraft(
            league_slug=slug,
            name=(request.form.get("name") or "Expansion Draft").strip()[:200] or "Expansion Draft",
            status="setup",
            goalie_rounds=max(0, int(request.form.get("goalie_rounds") or 1)),
            skater_rounds=max(0, int(request.form.get("skater_rounds") or 1)),
            max_players_lost_per_team=max(0, int(request.form.get("max_players_lost_per_team") or 1)),
            expansion_team_count=max(1, int(request.form.get("expansion_team_count") or 1)),
            timer_seconds=max(5, int(request.form.get("timer_seconds") or 120)),
            empty_queue_timer_seconds=max(5, int(request.form.get("empty_queue_timer_seconds") or 120)),
        )
        db.session.add(row)
        db.session.commit()
        flash("Expansion draft created.", "ok")
        return redirect(url_for("site_admin.admin_expansion_draft_hub_edit", draft_id=row.id))
    rows = list(
        db.session.scalars(
            select(LeagueExpansionDraft)
            .where(LeagueExpansionDraft.league_slug == slug)
            .order_by(LeagueExpansionDraft.id.desc())
        ).all()
    )
    return render_template("admin_expansion_draft_hub.html", league_slug=slug, drafts=rows)


@site_admin_bp.route("/expansion-draft-hub/<int:draft_id>", methods=["GET", "POST"])
@login_required
def admin_expansion_draft_hub_edit(draft_id: int):
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    row = db.session.get(LeagueExpansionDraft, draft_id)
    if not row or row.league_slug != slug:
        abort(404)

    from app.services.expansion_draft_state import (
        exempt_team_ids,
        expansion_franchise_ids_sorted,
        go_live,
        pause_timer,
        regenerate_slots,
        replace_eligible_players,
        resume_timer,
        resolve_admin_pick,
        set_exempt_team_ids,
        set_expansion_team_order,
        undo_last_pick,
    )
    from app.services.roster_team import (
        is_main_league_team,
        organization_main_team,
        player_exempt_from_expansion_pool,
    )

    if request.method == "POST":
        act = (request.form.get("action") or "").strip()
        if act == "save_settings" and row.status == "setup":
            exp_count = max(1, int(request.form.get("expansion_team_count") or 1))
            main_ids = {
                int(x)
                for x in db.session.scalars(
                    select(Team.id).where(or_(Team.fhm_league_id.is_(None), Team.fhm_league_id == 0))
                ).all()
            }
            raw_exp: set[int] = set()
            for pid_s in request.form.getlist("expansion_franchise"):
                if str(pid_s).strip().isdigit():
                    tid = int(pid_s)
                    if tid in main_ids:
                        raw_exp.add(tid)
            exp_list = sorted(raw_exp)
            err_msg: str | None = None
            if len(exp_list) != exp_count:
                err_msg = (
                    f"Select exactly {exp_count} BOWL expansion franchise(s) "
                    f"(you selected {len(exp_list)})."
                )
            g_first_raw = (request.form.get("goalie_phase_first_team_id") or "").strip()
            s_first_raw = (request.form.get("skater_phase_first_team_id") or "").strip()
            g_first: int | None = int(g_first_raw) if g_first_raw.isdigit() else None
            s_first: int | None = int(s_first_raw) if s_first_raw.isdigit() else None
            if not err_msg and len(exp_list) > 1:
                if g_first is None or g_first not in exp_list:
                    err_msg = "Choose which expansion franchise picks first in the goalie phase."
                elif s_first is None or s_first not in exp_list:
                    err_msg = "Choose which expansion franchise picks first in the skater phase."
            if not err_msg and len(exp_list) <= 1:
                g_first = exp_list[0] if len(exp_list) == 1 else None
                s_first = exp_list[0] if len(exp_list) == 1 else None

            if err_msg:
                flash(err_msg, "err")
            else:
                row.name = (request.form.get("name") or row.name).strip()[:200]
                row.goalie_rounds = max(0, int(request.form.get("goalie_rounds") or row.goalie_rounds))
                row.skater_rounds = max(0, int(request.form.get("skater_rounds") or row.skater_rounds))
                row.max_players_lost_per_team = max(
                    0, int(request.form.get("max_players_lost_per_team") or 1)
                )
                row.expansion_team_count = exp_count
                row.timer_seconds = max(5, int(request.form.get("timer_seconds") or row.timer_seconds))
                row.empty_queue_timer_seconds = max(
                    5, int(request.form.get("empty_queue_timer_seconds") or row.empty_queue_timer_seconds)
                )
                row.scheduled_start_at = _parse_scheduled_start(request.form.get("scheduled_start_at") or "")
                set_expansion_team_order(row, exp_list)
                row.goalie_phase_first_team_id = g_first
                row.skater_phase_first_team_id = s_first
                exempt: set[int] = set()
                for tm in db.session.scalars(
                    select(Team)
                    .where(or_(Team.fhm_league_id.is_(None), Team.fhm_league_id == 0))
                    .order_by(Team.name)
                ).all():
                    if request.form.get(f"exempt_team_{tm.id}") == "1":
                        exempt.add(int(tm.id))
                set_exempt_team_ids(row, exempt)
                flash("Settings saved.", "ok")
            db.session.commit()
        elif act == "regenerate_slots" and row.status == "setup":
            err = regenerate_slots(db.session, row)
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="expansion_draft_regen_slots",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Slots regenerated (goalie phase, then skater phase).", "ok")
            db.session.commit()
        elif act == "save_eligible" and row.status == "setup":
            exempt = exempt_team_ids(row)
            pids: set[int] = set()
            for pid_s in request.form.getlist("elig"):
                if not str(pid_s).strip().isdigit():
                    continue
                pid = int(pid_s)
                pl = db.session.get(Player, pid)
                if pl and not player_exempt_from_expansion_pool(db.session, pl, exempt):
                    pids.add(pid)
            replace_eligible_players(db.session, row, pids)
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="expansion_draft_save_eligible",
                    detail_json=json.dumps({"draft_id": row.id, "count": len(pids)}),
                )
            )
            db.session.commit()
            flash(f"Eligible pool updated ({len(pids)} players).", "ok")
        elif act == "go_live" and row.status == "setup":
            err = go_live(db.session, row, int(current_user.id))
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="expansion_draft_go_live",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Expansion draft is now live.", "ok")
            db.session.commit()
        elif act == "undo_pick" and row.status == "live":
            err = undo_last_pick(db.session, row)
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="expansion_draft_undo_pick",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Last pick removed.", "ok")
            db.session.commit()
        elif act == "pause_timer" and row.status == "live":
            err = pause_timer(db.session, row)
            if err:
                flash(err, "err")
            else:
                flash("Timer paused.", "ok")
            db.session.commit()
        elif act == "resume_timer" and row.status == "live":
            err = resume_timer(db.session, row)
            if err:
                flash(err, "err")
            else:
                flash("Timer resumed.", "ok")
            db.session.commit()
        elif act == "admin_pick" and row.status == "live":
            pid_raw = (request.form.get("player_id") or "").strip()
            if not pid_raw.isdigit():
                flash("Invalid player id.", "err")
            else:
                err = resolve_admin_pick(db.session, row, int(pid_raw), int(current_user.id))
                if err:
                    flash(err, "err")
                else:
                    db.session.add(
                        AdminAuditLog(
                            admin_user_id=int(current_user.id),
                            league_slug=slug,
                            action="expansion_draft_admin_pick",
                            detail_json=json.dumps({"draft_id": row.id, "player_id": int(pid_raw)}),
                        )
                    )
                    flash("Pick recorded.", "ok")
            db.session.commit()
        return redirect(url_for("site_admin.admin_expansion_draft_hub_edit", draft_id=draft_id))

    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    main_teams = [
        t
        for t in teams
        if t.fhm_league_id is None or int(t.fhm_league_id) == 0
    ]
    exempt = exempt_team_ids(row)
    expansion_franchise_ids = expansion_franchise_ids_sorted(row)
    elig_ids = {
        int(x)
        for x in db.session.scalars(
            select(LeagueExpansionDraftEligiblePlayer.player_id).where(
                LeagueExpansionDraftEligiblePlayer.league_expansion_draft_id == row.id
            )
        ).all()
    }
    players_all = list(
        db.session.scalars(
            select(Player)
            .where(Player.retired.is_(False))
            .options(joinedload(Player.contract), joinedload(Player.current_team))
            .order_by(Player.full_name.asc())
        ).unique().all()
    )
    expansion_org_players: dict[int, dict[str, list[Player]]] = {}
    player_ids = [int(p.id) for p in players_all]
    prospect_by_pid: dict[int, Prospect] = {}
    if player_ids:
        for pr in db.session.scalars(select(Prospect).where(Prospect.player_id.in_(player_ids))).all():
            if pr.player_id is None:
                continue
            pid = int(pr.player_id)
            if pid not in prospect_by_pid:
                prospect_by_pid[pid] = pr
    for pl in players_all:
        if pl.contract is None:
            continue
        org = organization_main_team(
            db.session, pl, prospect=prospect_by_pid.get(int(pl.id))
        )
        if org is None:
            continue
        tid = int(org.id)
        ct = pl.current_team
        if ct is not None and is_main_league_team(ct) and int(ct.id) == tid:
            bucket = "main"
        else:
            bucket = "minors"
        expansion_org_players.setdefault(tid, {"main": [], "minors": []})[bucket].append(pl)
    for _tid, buckets in expansion_org_players.items():
        buckets["main"].sort(key=lambda p: (p.full_name or "").lower())
        buckets["minors"].sort(key=lambda p: (p.full_name or "").lower())

    slots = list(
        db.session.scalars(
            select(LeagueExpansionDraftSlot)
            .where(LeagueExpansionDraftSlot.league_expansion_draft_id == row.id)
            .order_by(LeagueExpansionDraftSlot.overall_pick)
        ).all()
    )
    sched = ""
    if row.scheduled_start_at:
        sched = row.scheduled_start_at.strftime("%Y-%m-%dT%H:%M")
    return render_template(
        "admin_expansion_draft_hub_edit.html",
        league_slug=slug,
        draft=row,
        teams=teams,
        main_teams=main_teams,
        exempt_ids=exempt,
        eligible_ids=elig_ids,
        expansion_org_players=expansion_org_players,
        expansion_franchise_ids=expansion_franchise_ids,
        slots=slots,
        sched_value=sched,
    )
