"""GM + admin site features (league mounts only): AP, news, redemptions."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta
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
    has_admin_role,
    require_admin,
    require_admin_role,
)
from app.config import Config, is_racing_league, league_display_name, league_group_for_slug
from app.logo_urls import team_logo_url_for_team
from app.league_db import db
from app.models import (
    FranchiseTeamIdentity,
    HallOfFameMember,
    HistoryAllStar,
    HistoryAward,
    Player,
    PlayerContract,
    Prospect,
    Season,
    Team,
    TeamStanding,
    TeamRetiredNumber,
    TeamSeasonRecord,
    TeamVictoryBanner,
    TradeLogEntry,
)
from app.services.ap_multileague import team_id_for_slug_in_league
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.gm_messaging import (
    active_peer_membership,
    create_gm_message,
    gm_discord_name,
    gm_display_name,
    inbox_threads,
    list_other_active_gms,
    mark_thread_read,
    send_gm_message,
    thread_messages,
)
from app.services.gm_notifications import (
    list_notifications,
    notify_all_gms_admin_article,
    notify_news_approved,
    notify_news_denied,
    notify_redemption_approved,
    notify_redemption_denied,
    notify_rfa_awaiting_equalization,
    notify_rfa_awaiting_match,
    notify_rfa_offer_outcome,
    notify_rfa_original_team_decision,
    notify_rfa_player_rejected,
    notify_trade_outcome_partner,
    notify_trade_outcome_proposer,
)
from app.services.staff_catalog import (
    build_staff_profile_view,
    get_staff_profile,
    staff_role_label,
)
from app.services.staff_images import staff_image_url, staff_placeholder_url
from app.services.staff_transactions import (
    admin_fire_staff,
    admin_hire_staff,
    admin_retire_staff,
    admin_save_staff_contract,
    admin_set_team_staff_penalty_total,
    expire_stale_staff_contracts,
    transaction_headline_for_entry,
)
from app.services.franchise_identities import (
    identity_logo_url,
    norm_fhm_team_id,
    sync_franchise_identities_from_csv_if_needed,
)
from app.services.season_team_logo_bundle import get_season_team_logo_bundle
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
from app.mail_util import send_site_email
from app.services.import_validation import build_import_validation_report
from app.services.join_league import (
    configured_join_team_options,
    join_available_teams_path,
    mail_settings_summary,
    save_join_team_options,
)
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
    DISCORD_CHANNEL_FANOUT_EVENT_KEYS,
    add_discord_route,
    build_league_public_url,
    build_news_article_public_url,
    delete_discord_route,
    enqueue_discord_event,
    get_league_bot_config,
    gm_role_mention_for_league,
    canonical_discord_bot_name,
    list_game_boxscore_team_channels,
    list_heartbeats,
    list_discord_routes,
    news_article_discord_payload,
    resolve_news_article_team,
    trade_request_discord_payload,
    prune_obsolete_discord_bot_heartbeats,
    list_outbound_events,
    team_fields_for_discord,
    update_discord_routes,
    update_game_boxscore_team_channels,
    update_league_bot_config,
)
from app.services.prediction_center import build_prediction_snapshot
from app.services.media_kit import build_media_kit_snapshot
from app.services.member_digest import build_member_watchlist_digest
from app.services.seasons import get_current_season, season_age_reference_date, season_display_label, season_with_imported_data_fallback
from app.services.season_team_logo_bundle import dashboard_team_logo_url
from app.services.cap_strike_penalties import (
    STRIKE_TO_ROUND,
    active_cycle_year,
    save_cycle_strikes,
    strike_grid_rows,
)
from app.services.ap_service import (
    active_redemption_items,
    add_ledger_entry,
    approve_redemption_request,
    league_ledger_page,
    parse_ledger_list_params,
    new_redemption_token,
    publish_news_and_maybe_award_ap,
    team_ap_balance,
)
from app.sqlite_retry import commit_with_sqlite_retry, flush_with_sqlite_retry, write_with_sqlite_retry
from app.services.export_attendance import (
    build_attendance_tracker_payload,
    maybe_send_export_gap_warning,
    parse_export_date,
    register_export_attendance,
)
from app.services.draft_pick_ownership import (
    build_draft_pick_ownership_year_grid,
    complete_stale_draft_pick_ownership_panels,
    draft_pick_ownership_exists,
    draft_pick_teams_for_grid,
    ensure_draft_pick_ownership_panels,
    reset_calendar_seeded_panels_if_needed,
    save_draft_pick_ownership_year_grid,
)
from app.services.discord_events import team_fields_for_discord
from app.services.trade_ai_opinion import (
    fetch_logged_trade_ai_opinion,
    fetch_trade_ai_opinion,
    recent_trades_prompt_block,
)
from app.services.trade_log import resolve_trade_log_row, trade_log_rows as build_trade_log_rows
from app.services.trade_market import (
    BUYING_CATEGORIES,
    active_buying_rows,
    active_selling_rows,
    annotate_trade_market_need_matches,
    annotate_trade_market_watchlist,
    build_trade_market_activity_ticker,
    buying_discord_update_should_enqueue,
    cleanup_stale_selling_listings,
    maybe_enqueue_buying_discord,
    maybe_enqueue_selling_discord,
    replace_buying_needs,
    replace_selling_listings,
    selling_discord_update_should_enqueue,
    selectable_selling_assets,
    sort_selling_rows,
    user_watchlist_team_ids,
)
from app.services.trade_tool import (
    STATUS_COMMISSIONER_DECLINED,
    STATUS_PENDING_COMMISSIONER,
    STATUS_PUBLISHED,
    format_trade_discord_body,
    format_ledger_summary,
    gm_user_id_for_team,
    parse_ledger_payload,
    publish_trade_news_articles,
    publish_trade_proposal,
    trade_assets_for_team,
    trade_tool_draft_round_cap,
    validate_ledger,
)
from app.site_models import (
    AdminAuditLog,
    ApRedemptionCatalog,
    ApRedemptionRequest,
    BoostLotteryTeamResult,
    GmInAppNotification,
    GmApprovalRequest,
    GmLeagueMembership,
    GmTradeProposal,
    DiscordDirectMessageEvent,
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
    DraftPickOwnershipYear,
    SiteAnnouncement,
    StoryPublishSchedule,
    TradeMarketDraftPickOwnership,
    TradeMarketBuyingNeed,
    TradeMarketListing,
    AwardsVotingCycle,
    MemberWatchlistItem,
    AdminUndoAction,
    DiscordOutboundEvent,
    RfaOfferRequest,
    TeamStaffBudget,
    TeamCapPenalty,
    User,
)
from app.services.staff_salaries import (
    current_season_start_year,
    main_league_teams,
    staff_salary_context,
)
from app.services.league_finances import (
    build_league_finances_context,
    cap_penalty_admin_context,
)
from app.services.salary_cap_schedule import (
    build_cap_panels_view,
    cap_for_season,
    save_salary_cap_panel,
    sync_salary_cap_schedule_rollover,
)
from app.services.rfa_offers import (
    CATEGORY_LABELS,
    CATEGORY_TOOLTIPS,
    HAPPINESS_LEVELS,
    compensation_for_offer,
    compensation_panel_dict,
    compensation_reference_rows,
    create_rfa_offer_request,
    happiness_label,
    list_rfa_candidates,
    roll_group_iii_allows_match,
    roll_player_accepts,
    status_label,
    validate_offer_submission,
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
    for g in ("roster", "unsigned", "draft_picks"):
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


def _audit(admin_action: str, detail: dict) -> None:
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=_league_slug(),
            action=admin_action,
            detail_json=json.dumps(detail),
        )
    )


def _membership():
    return active_membership_for_league(current_user, _league_slug())


def _gm_membership_for_team(slug: str, team_id: int) -> GmLeagueMembership | None:
    return db.session.scalar(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.team_id == int(team_id),
            GmLeagueMembership.status == "active",
        ).limit(1)
    )


def _gm_user_for_team(slug: str, team_id: int) -> User | None:
    mem = _gm_membership_for_team(slug, team_id)
    if not mem:
        return None
    return db.session.get(User, int(mem.user_id))


def _is_site_admin() -> bool:
    """League-mount admins (any admin role) can use trade pages on all three sites."""
    if not current_user.is_authenticated:
        return False
    return has_admin_role(current_user)


def _ap_ledger_template_context(
    slug: str,
    *,
    teams: list[Team],
    form_endpoint: str,
) -> dict:
    """Ledger filters, rows, and pagination URLs for AP page templates."""
    page, team_id, kind = parse_ledger_list_params(request.args)
    ledger = league_ledger_page(slug, page=page, team_id=team_id, kind=kind)

    def _ledger_page_url(target_page: int) -> str | None:
        if target_page < 1 or target_page > int(ledger["total_pages"]):
            return None
        params: dict[str, int | str] = {}
        if target_page > 1:
            params["ledger_page"] = target_page
        if team_id is not None:
            params["ledger_team"] = int(team_id)
        if kind:
            params["ledger_kind"] = kind
        return url_for(form_endpoint, **params)

    return {
        "ledger_rows": ledger["rows"],
        "ledger_page": ledger["page"],
        "ledger_total_pages": ledger["total_pages"],
        "ledger_total_count": ledger["total_count"],
        "ledger_per_page": ledger["per_page"],
        "ledger_team_id": team_id,
        "ledger_kind": kind or "",
        "ledger_teams": teams,
        "ledger_form_action": url_for(form_endpoint),
        "ledger_reset_url": url_for(form_endpoint),
        "ledger_prev_url": _ledger_page_url(int(ledger["page"]) - 1),
        "ledger_next_url": _ledger_page_url(int(ledger["page"]) + 1),
    }


def _can_view_action_points_page(mem=None) -> bool:
    """Action Points page: active GMs and league admins only."""
    return mem is not None or _is_site_admin()


def _can_use_official_trade_tool() -> bool:
    """League/super admins only — official Trade Tool entry and publish."""
    if not current_user.is_authenticated:
        return False
    return has_admin_role(current_user, ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)


def _can_use_official_staff_tool() -> bool:
    """League/super admins only — Staff Hire/Fire league office tool."""
    return _can_use_official_trade_tool()


def _admin_staff_team_id() -> int | None:
    if not _can_use_official_staff_tool():
        return None
    tid = request.args.get("admin_team_id", type=int) or request.form.get(
        "admin_team_id", type=int
    )
    return int(tid) if tid and tid > 0 else None


def _publish_admin_staff_transaction(
    *,
    slug: str,
    team: Team | None,
    entry,
    action: str,
) -> None:
    """News article + Discord for direct admin staff hire/fire/retire."""
    role_l = staff_role_label(entry.role)
    title = transaction_headline_for_entry(entry, team, action=action)
    db.session.add(
        NewsArticle(
            league_slug=slug,
            team_id=int(entry.team_id),
            title=title,
            body=(
                f"Staff {action} — {entry.staff_name} ({role_l}).\n"
                f"Processed by league office."
            ),
            category="transactions",
            author_user_id=int(current_user.id),
            status="published",
            published_at=datetime.utcnow(),
        )
    )
    discord_action = "hired" if action == "hire" else ("retired" if action == "retire" else "fired")
    body = f"{entry.staff_name} ({role_l})"
    _enqueue_discord_event(
        "staff_transaction_posted",
        {
            "request_id": int(entry.id or 0),
            "action": discord_action,
            "staff_name": str(entry.staff_name or ""),
            "role_label": role_l,
            "gm_name": "",
            "title": "Staff hired" if discord_action == "hired" else (
                "Staff retired" if discord_action == "retired" else "Staff fired"
            ),
            "body": body,
            "body_preview": body[:280],
            "has_image": False,
            **team_fields_for_discord(team),
        },
        source_type="staff_roster_entry",
        source_id=int(entry.id or 0),
    )


def _trade_page_allowed(mem=None) -> bool:
    """AI Trade Tool and related read-only pages: active GMs and site admins."""
    return mem is not None or _is_site_admin()


def _can_load_trade_assets(mem) -> bool:
    """Trade asset JSON for official tool (admin + team) or AI hypothetical tool (GM + ?ai=1)."""
    ai = (request.args.get("ai") or "").strip().lower()
    ai_mode = ai in ("1", "true", "yes")
    if _can_use_official_trade_tool():
        return _admin_trade_team_id() is not None
    if mem is None:
        return False
    return ai_mode


def _can_use_gm_messaging() -> bool:
    """Active GMs and site admins may use the in-league GM messages inbox."""
    if not current_user.is_authenticated:
        return False
    if _is_site_admin():
        return True
    return _membership() is not None


def _active_trade_memberships(slug: str) -> list[tuple[GmLeagueMembership, User]]:
    rows = list(
        db.session.execute(
            select(GmLeagueMembership, User)
            .join(User, User.id == GmLeagueMembership.user_id)
            .where(
                GmLeagueMembership.league_slug == slug,
                GmLeagueMembership.status == "active",
            )
        ).all()
    )
    return [(m, u) for m, u in rows]


def _trade_partner_options(
    slug: str,
    *,
    exclude_user_id: int | None = None,
    exclude_team_id: int | None = None,
) -> list[dict[str, object]]:
    memberships = _active_trade_memberships(slug)
    team_ids = {int(m.team_id) for m, _ in memberships if m.team_id is not None}
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
            teams_by_id[int(t.id)] = t

    seen_team_ids: set[int] = set()
    options: list[dict[str, object]] = []
    for m, u in memberships:
        tid = int(m.team_id)
        if exclude_user_id is not None and int(u.id) == int(exclude_user_id):
            continue
        if exclude_team_id is not None and tid == int(exclude_team_id):
            continue
        if tid in seen_team_ids:
            continue
        seen_team_ids.add(tid)
        tm = teams_by_id.get(tid)
        options.append(
            {
                "user_id": int(u.id),
                "team_id": tid,
                "team_name": tm.full_display_name() if tm else f"Team {tid}",
                "gm_name": gm_display_name(u),
            }
        )
    options.sort(key=lambda r: str(r.get("team_name") or "").lower())
    return options


def _trade_team_options(*, exclude_team_id: int | None = None) -> list[dict[str, object]]:
    """All league teams for admin "act as team" selectors."""
    active_by_team: dict[int, User] = {}
    for m, u in _active_trade_memberships(_league_slug()):
        if m.team_id is not None and int(m.team_id) not in active_by_team:
            active_by_team[int(m.team_id)] = u

    rows: list[dict[str, object]] = []
    teams = db.session.scalars(select(Team).order_by(Team.name.asc())).all()
    for tm in teams:
        tid = int(tm.id)
        if exclude_team_id is not None and tid == int(exclude_team_id):
            continue
        gm_user = active_by_team.get(tid)
        rows.append(
            {
                "user_id": int(gm_user.id) if gm_user else None,
                "team_id": tid,
                "team_name": tm.full_display_name(),
                "gm_name": gm_display_name(gm_user) if gm_user else "No active GM",
            }
        )
    rows.sort(key=lambda r: str(r.get("team_name") or "").lower())
    return rows


def _admin_trade_team_id() -> int | None:
    if not _can_use_official_trade_tool():
        return None
    tid = request.args.get("admin_team_id", type=int) or request.form.get(
        "admin_team_id", type=int
    )
    return int(tid) if tid and tid > 0 else None


def _trade_user_id_for_team(slug: str, team_id: int, *, fallback_user_id: int) -> int:
    uid = gm_user_id_for_team(db.session, slug, int(team_id))
    return int(uid) if uid is not None else int(fallback_user_id)


def _franchise_identity_team_options() -> list[Team]:
    return list(db.session.scalars(select(Team).order_by(Team.name.asc(), Team.id.asc())).all())


def _join_league_availability_rows() -> tuple[list[dict[str, object]], list[str]]:
    configured_names, _ = configured_join_team_options()
    configured_keys = {name.casefold() for name in configured_names}

    active_by_team: dict[int, User] = {}
    slug = _league_slug()
    for mem, user in _active_trade_memberships(slug):
        if mem.team_id is not None and int(mem.team_id) not in active_by_team:
            active_by_team[int(mem.team_id)] = user

    rows: list[dict[str, object]] = []
    seen_configured: set[str] = set()
    for team in main_league_teams(db.session):
        team_name = team.full_display_name()
        key = team_name.casefold()
        seen_configured.add(key)
        gm_user = active_by_team.get(int(team.id))
        rows.append(
            {
                "team": team,
                "team_name": team_name,
                "is_open": key in configured_keys,
                "has_active_gm": gm_user is not None,
                "gm_name": gm_display_name(gm_user) if gm_user else "",
            }
        )

    rows.sort(key=lambda r: str(r.get("team_name") or "").lower())
    stale_options = [name for name in configured_names if name.casefold() not in seen_configured]
    return rows, stale_options


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


def _enqueue_discord_event(
    event_key: str,
    payload: dict,
    *,
    source_type: str | None = None,
    source_id: str | int | None = None,
) -> None:
    slug = _league_slug()
    try:
        enqueue_discord_event(
            db.session,
            league_slug=slug,
            event_key=event_key,
            payload=payload or {},
            created_by_user_id=int(current_user.id) if getattr(current_user, "is_authenticated", False) else None,
            source_type=source_type,
            source_id=source_id,
        )
    except Exception:
        # Never block primary admin flows on outbound queue writes.
        pass


def _discord_mention_for_user(user: User | None) -> str:
    if user is None or getattr(user, "revoked_at", None) is not None:
        return ""
    discord_id = str(getattr(user, "discord_user_id", "") or "").strip()
    if len(discord_id) < 17 or len(discord_id) > 20 or not discord_id.isdigit():
        return ""
    return f"<@{discord_id}>"


def _trade_gm_mentions(proposal: GmTradeProposal) -> str:
    mentions: list[str] = []
    seen: set[str] = set()
    for user_id in (getattr(proposal, "from_user_id", None), getattr(proposal, "to_user_id", None)):
        if user_id is None:
            continue
        mention = _discord_mention_for_user(db.session.get(User, int(user_id)))
        if mention and mention not in seen:
            mentions.append(mention)
            seen.add(mention)
    return " ".join(mentions)


def _enqueue_confirmed_trade_discord(
    *,
    proposal: GmTradeProposal,
    proposal_id: int,
    article_id: int | None,
    from_team: Team | None,
    to_team: Team | None,
    team: Team | None,
) -> None:
    if not article_id:
        return
    slug = _league_slug()
    trade_article = db.session.get(NewsArticle, int(article_id))
    if trade_article is None:
        return
    payload = news_article_discord_payload(
        trade_article,
        category=str(trade_article.category or ""),
        proposal_id=int(proposal_id),
        url=build_news_article_public_url(slug, int(trade_article.id)),
        **team_fields_for_discord(team),
    )
    payload["body"] = format_trade_discord_body(db.session, proposal, from_team, to_team)
    payload["body_preview"] = str(payload["body"])[:280]
    gm_mentions = _trade_gm_mentions(proposal)
    if gm_mentions:
        payload["gm_mentions"] = gm_mentions
    _enqueue_discord_event(
        "confirmed_trade",
        payload,
        source_type="confirmed_trade",
        source_id=int(proposal_id),
    )


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


@site_gm_bp.get("/attendance-tracker")
@login_required
def export_attendance_tracker():
    """Rolling 45-day GM export attendance grid for active GMs and league admins."""
    slug = _league_slug()
    if not _membership() and not _is_site_admin():
        flash("Attendance Tracker is available to active GMs and league admins.", "err")
        return redirect(url_for("main.home"))
    tracker = build_attendance_tracker_payload(
        db.session,
        slug,
        logo_resolver=team_logo_url_for_team,
    )
    return render_template(
        "export_attendance_tracker.html",
        tracker=tracker,
        membership=_membership(),
    )


@site_gm_bp.get("/help-tips")
@login_required
def gm_help_tips_page():
    """FHM help and tips for active GMs and league admins."""
    if not _membership() and not _is_site_admin():
        flash("Help/Tips is available to active GMs and league admins.", "err")
        return redirect(url_for("main.home"))
    return render_template("gm_help_tips.html")


@site_gm_bp.get("/action-points")
@login_required
def action_points_page():
    slug = _league_slug()
    mem = _membership()
    if not _can_view_action_points_page(mem):
        flash("Action Points are available to active GMs and league admins.", "err")
        return redirect(url_for("main.home"))
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    rows = []
    for t in teams:
        rows.append({"team": t, "balance": team_ap_balance(slug, t.id)})
    rows.sort(key=lambda r: (-r["balance"], r["team"].name or ""))
    catalog = active_redemption_items(slug) if mem else []
    bal = team_ap_balance(slug, mem.team_id) if mem else None
    from app.services.ap_redemption_forms import (
        catalog_item_form_key,
        catalog_item_has_detail_form,
        catalog_item_allows_quantity,
        form_fields_for_key,
        team_select_options,
    )

    catalog_rows = []
    team_options: list[tuple[str, str]] = []
    if mem:
        team_options = team_select_options(db.session)
        for it in catalog:
            fk = catalog_item_form_key(it.title)
            catalog_rows.append(
                {
                    "item": it,
                    "form_key": fk,
                    "has_form": catalog_item_has_detail_form(fk),
                    "allows_quantity": catalog_item_allows_quantity(it.title),
                    "fields": form_fields_for_key(fk),
                }
            )
    return render_template(
        "action_points.html",
        rows=rows,
        membership=mem,
        catalog_rows=catalog_rows,
        team_options=team_options,
        balance=bal,
        **_ap_ledger_template_context(
            slug,
            teams=list(teams),
            form_endpoint="site_gm.action_points_page",
        ),
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
    from app.services.ap_redemption_forms import (
        catalog_item_allows_quantity,
        catalog_item_form_key,
        catalog_item_has_detail_form,
        extract_raw_details_for_catalog_id,
        format_details_summary,
        form_fields_for_key,
        parse_catalog_item_details,
    )

    lines = []
    total = 0
    for it in items:
        if not it.is_active or it.league_group != group:
            continue
        form_key = catalog_item_form_key(it.title)
        details: dict = {}
        quantity = 1
        if catalog_item_allows_quantity(it.title):
            qty_raw = request.form.get(f"catalog_qty_{int(it.id)}", "1")
            try:
                quantity = int(qty_raw)
            except (TypeError, ValueError):
                quantity = 0
            if quantity < 1 or quantity > 99:
                flash(f"{it.title}: enter a quantity from 1 to 99.", "err")
                return redirect(url_for("site_gm.action_points_page"))
        if catalog_item_has_detail_form(form_key):
            raw = extract_raw_details_for_catalog_id(request.form, int(it.id))
            details, err = parse_catalog_item_details(
                form_key, raw, session=db.session
            )
            if err:
                flash(f"{it.title}: {err}", "err")
                return redirect(url_for("site_gm.action_points_page"))
        if quantity > 1:
            details = dict(details)
            details["quantity"] = quantity
        line = {
            "id": it.id,
            "title": it.title,
            "cost": int(it.cost_ap) * quantity,
            "unit_cost": int(it.cost_ap),
            "quantity": quantity,
            "details": details,
            "summary": format_details_summary(details) if details else "",
        }
        lines.append(line)
        total += int(it.cost_ap) * quantity
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

        redeem_team = db.session.get(Team, int(mem.team_id))
        notify_ap_redemption_pending(
            league_slug=slug,
            league_display_name=_league_display_name(slug),
            request_id=int(req.id),
            user_email=str(current_user.email or ""),
            gm_name=gm_discord_name(current_user),
            team_name=redeem_team.full_display_name() if redeem_team else f"Team {mem.team_id}",
            team_id=int(mem.team_id),
            total_ap=int(total),
        )
    except Exception as exc:
        current_app.logger.warning("Admin notify (AP redemption): %s", exc)
    commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
            try:
                from app.services.admin_review_notify import notify_news_pending_review

                notify_news_pending_review(
                    league_slug=slug,
                    league_display_name=str(current_app.config.get("LEAGUE_DISPLAY_NAME", slug)),
                    article_id=int(art.id),
                    author_email=str(current_user.email or ""),
                    title=str(art.title or ""),
                )
                commit_with_sqlite_retry(db.session)
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
    if not _can_use_official_trade_tool():
        flash("The Trade Tool is available to league administrators only.", "err")
        return redirect(url_for("main.home"))
    admin_team_id = _admin_trade_team_id()
    my_team_id = admin_team_id
    my_team = db.session.get(Team, int(my_team_id)) if my_team_id else None
    partner_options = _trade_team_options(exclude_team_id=my_team_id)
    admin_team_options = _trade_team_options()
    recent = list(
        db.session.scalars(
            select(GmTradeProposal)
            .where(
                GmTradeProposal.league_slug == slug,
                GmTradeProposal.status == STATUS_PUBLISHED,
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
        membership=None,
        my_team=my_team,
        my_team_logo_url=my_team_logo_url,
        partner_options=partner_options,
        admin_team_options=admin_team_options,
        admin_team_id=admin_team_id,
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
    admin_team_id = _admin_trade_team_id()
    if not _can_load_trade_assets(mem):
        abort(404)
    if _can_use_official_trade_tool():
        if not admin_team_id:
            return jsonify({"error": "admin_team_id required"}), 400
        left_team_id = int(admin_team_id)
    else:
        left_team_id = int(mem.team_id)
    raw_tid = request.args.get("partner_team_id", type=int)
    if not raw_tid or raw_tid <= 0:
        return jsonify({"error": "partner_team_id required"}), 400
    ai_mode = (request.args.get("ai") or "").strip().lower() in ("1", "true", "yes")
    peer = None
    if _can_use_official_trade_tool() or ai_mode:
        if int(raw_tid) == int(left_team_id) or db.session.get(Team, int(raw_tid)) is None:
            return jsonify({"error": "Invalid trading partner team."}), 400
        peer = _gm_membership_for_team(slug, int(raw_tid))
    else:
        peer = db.session.scalar(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == slug,
                GmLeagueMembership.team_id == int(raw_tid),
                GmLeagueMembership.status == "active",
                GmLeagueMembership.team_id != int(left_team_id),
            )
        )
        if not peer:
            return jsonify({"error": "Invalid trading partner team."}), 400
    raw_dir = _trade_tool_raw_dir()
    completed_panels = complete_stale_draft_pick_ownership_panels(
        db.session,
        db.session,
        league_slug=slug,
    )
    if completed_panels:
        commit_with_sqlite_retry(db.session)
    left = trade_assets_for_team(
        db.session, int(left_team_id), raw_dir=raw_dir, league_slug=slug
    )
    right = trade_assets_for_team(
        db.session, int(raw_tid), raw_dir=raw_dir, league_slug=slug
    )
    _finalize_trade_asset_side_urls(left)
    _finalize_trade_asset_side_urls(right)
    p_user = db.session.get(User, int(peer.user_id)) if peer else None
    p_team = db.session.get(Team, int(raw_tid))
    draft_cap = trade_tool_draft_round_cap(db.session, slug)
    if (request.args.get("ai") or "").strip() in ("1", "true", "yes"):
        draft_cap = min(8, int(draft_cap))
    player_tpl = url_for("main.player_page", player_id=_TRADE_PLAYER_URL_PLACEHOLDER_ID)
    return jsonify(
        {
            "left_team_id": int(left_team_id),
            "right_team_id": int(raw_tid),
            "left": left,
            "right": right,
            "draft_pick_ownership_available": draft_pick_ownership_exists(
                db.session, league_slug=slug
            ),
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
    if not _can_use_official_trade_tool():
        flash("The Trade Tool is available to league administrators only.", "err")
        return redirect(url_for("main.home"))
    admin_team_id = _admin_trade_team_id()
    if not admin_team_id:
        flash("Choose the left-side team before publishing.", "err")
        return redirect(url_for("site_gm.trade_tool"))
    left_team_id = int(admin_team_id)
    return_url = url_for("site_gm.trade_tool", admin_team_id=left_team_id)
    partner_team_id = request.form.get("partner_team_id", type=int)
    ledger_raw = (request.form.get("ledger_json") or "").strip()
    notes = (request.form.get("notes") or "").strip()
    if not partner_team_id or partner_team_id <= 0:
        flash("Choose a trading partner team.", "err")
        return redirect(return_url)
    if int(partner_team_id) == int(left_team_id) or db.session.get(Team, int(partner_team_id)) is None:
        flash("Choose a valid trading partner team.", "err")
        return redirect(return_url)
    left_out, right_out = parse_ledger_payload(ledger_raw)
    err = validate_ledger(
        db.session,
        int(left_team_id),
        int(partner_team_id),
        left_out,
        right_out,
        raw_dir=_trade_tool_raw_dir(),
        league_slug=slug,
    )
    if err:
        flash(err, "err")
        return redirect(return_url)
    payload_obj = {"from_left_to_right": left_out, "from_right_to_left": right_out}
    admin_uid = int(current_user.id)
    from_uid = _trade_user_id_for_team(slug, left_team_id, fallback_user_id=admin_uid)
    to_uid = _trade_user_id_for_team(slug, int(partner_team_id), fallback_user_id=admin_uid)
    raw_dir = _trade_tool_raw_dir()
    published_article_id: int | None = None
    moved_count = 0
    proposal_id: int | None = None

    def _persist_and_publish_trade() -> None:
        nonlocal published_article_id, moved_count, proposal_id
        prop = GmTradeProposal(
            league_slug=slug,
            from_user_id=from_uid,
            from_team_id=int(left_team_id),
            to_user_id=to_uid,
            to_team_id=int(partner_team_id),
            status=STATUS_PENDING_COMMISSIONER,
            ledger_json=json.dumps(payload_obj),
            notes=notes[:8000],
        )
        db.session.add(prop)
        db.session.flush()
        proposal_id = int(prop.id)
        published_article_id, moved_rows, pub_err = publish_trade_proposal(
            db.session,
            db.session,
            league_slug=slug,
            proposal=prop,
            commissioner_user_id=admin_uid,
            raw_dir=raw_dir,
            notify_gms=True,
        )
        if pub_err:
            raise ValueError(pub_err)
        moved_count = len(moved_rows)

    try:
        write_with_sqlite_retry(db.session, _persist_and_publish_trade)
    except ValueError as exc:
        flash(str(exc), "err")
        return redirect(return_url)
    from_team = db.session.get(Team, int(left_team_id))
    to_team = db.session.get(Team, int(partner_team_id))
    if published_article_id and proposal_id:
        prop = db.session.get(GmTradeProposal, int(proposal_id))
        if prop:
            _enqueue_confirmed_trade_discord(
                proposal=prop,
                proposal_id=int(proposal_id),
                article_id=int(published_article_id),
                from_team=from_team,
                to_team=to_team,
                team=from_team,
            )
            commit_with_sqlite_retry(db.session)
    msg = "Trade published on the site for both teams."
    if moved_count:
        msg += f" Draft ownership updated for {moved_count} pick(s)."
    flash(msg, "ok")
    return redirect(return_url)


def _trade_market_prev_discord_hash(rows, attr: str = "discord_payload_hash") -> str:
    for row in rows:
        h = str(getattr(row, attr, None) or "").strip()
        if h:
            return h
    return ""


@site_gm_bp.route("/trade-market", methods=["GET"])
def trade_market_page():
    slug = _league_slug()
    mem = _membership() if current_user.is_authenticated else None
    admin_team_id = _admin_trade_team_id() if mem is None and _is_site_admin() else None
    my_team_id = int(mem.team_id) if mem else admin_team_id
    my_team = db.session.get(Team, int(my_team_id)) if my_team_id else None
    admin_team_options = _trade_team_options() if mem is None and _is_site_admin() else []
    is_site_admin = _is_site_admin()
    sort_key = (request.args.get("sort") or "updated").strip()
    sort_order = (request.args.get("order") or "desc").strip()
    cleaned = cleanup_stale_selling_listings(
        db.session,
        db.session,
        league_slug=slug,
        raw_dir=_trade_tool_raw_dir(),
    )
    if cleaned:
        commit_with_sqlite_retry(db.session)
    selling_rows = sort_selling_rows(
        active_selling_rows(db.session, db.session, league_slug=slug),
        sort_key=sort_key,
        order=sort_order,
    )
    buying_rows = active_buying_rows(db.session, db.session, league_slug=slug)
    watchlist_team_ids: set[int] = set()
    my_buying_categories: set[str] = set()
    if current_user.is_authenticated:
        watchlist_team_ids = user_watchlist_team_ids(
            db.session, league_slug=slug, user_id=int(current_user.id)
        )
        if my_team_id:
            my_buy_row = next(
                (r for r in buying_rows if int(r.get("team_id") or 0) == int(my_team_id)),
                None,
            )
            if my_buy_row:
                my_buying_categories = {str(c) for c in (my_buy_row.get("categories") or [])}
    annotate_trade_market_watchlist(selling_rows, watchlist_team_ids=watchlist_team_ids)
    annotate_trade_market_watchlist(buying_rows, watchlist_team_ids=watchlist_team_ids)
    annotate_trade_market_need_matches(
        selling_rows, my_buying_categories=my_buying_categories
    )
    activity_ticker = build_trade_market_activity_ticker(selling_rows, buying_rows)
    if not current_user.is_authenticated:
        for row in selling_rows:
            row["gm_name"] = ""
            row["user_id"] = 0
        for row in buying_rows:
            row["gm_name"] = ""
            row["user_id"] = 0
    my_listings = [
        r
        for r in selling_rows
        if my_team_id and int(r.get("team_id") or 0) == int(my_team_id)
    ]
    my_buying = next(
        (
            r
            for r in buying_rows
            if my_team_id and int(r.get("team_id") or 0) == int(my_team_id)
        ),
        None,
    )
    market_team_ids = {
        int(r.get("team_id") or 0)
        for r in [*selling_rows, *buying_rows]
        if int(r.get("team_id") or 0) > 0
    }
    market_teams = {
        int(t.id): t
        for t in db.session.scalars(select(Team).where(Team.id.in_(market_team_ids))).all()
    } if market_team_ids else {}

    def _team_block_defaults(tid: int, row: dict[str, object]) -> dict[str, object]:
        team = market_teams.get(int(tid))
        return {
            "team_id": tid,
            "team_name": (team.full_display_name() if team else None) or row.get("team_name") or f"Team {tid}",
            "team_abbr": (team.abbreviation if team else "") or "",
            "team_logo_url": team_logo_url_for_team(team) if team else "",
            "team_color": (team.primary_color if team else "") or "",
            "team_text_color": (team.text_color if team else "") or "",
            "gm_name": row.get("gm_name") or "",
            "user_id": int(row.get("user_id") or 0),
            "selling": [],
            "buying": None,
            "updated_at": row.get("updated_at"),
        }

    team_market_rows: dict[int, dict[str, object]] = {}
    for row in selling_rows:
        tid = int(row.get("team_id") or 0)
        if tid <= 0:
            continue
        team_market_rows.setdefault(tid, _team_block_defaults(tid, row))
        team_market_rows[tid]["selling"].append(row)  # type: ignore[index]
        if row.get("updated_at") and (
            not team_market_rows[tid].get("updated_at")
            or row.get("updated_at") > team_market_rows[tid].get("updated_at")
        ):
            team_market_rows[tid]["updated_at"] = row.get("updated_at")
    for row in buying_rows:
        tid = int(row.get("team_id") or 0)
        if tid <= 0:
            continue
        entry = team_market_rows.setdefault(tid, _team_block_defaults(tid, row))
        entry["buying"] = row
        if not entry.get("gm_name"):
            entry["gm_name"] = row.get("gm_name") or ""
        if not entry.get("user_id"):
            entry["user_id"] = int(row.get("user_id") or 0)
        if row.get("updated_at") and (
            not entry.get("updated_at") or row.get("updated_at") > entry.get("updated_at")
        ):
            entry["updated_at"] = row.get("updated_at")
    market_team_rows = sorted(
        team_market_rows.values(),
        key=lambda r: str(r.get("team_name") or "").casefold(),
    )
    player_page_url_template = url_for(
        "main.player_page", player_id=_TRADE_PLAYER_URL_PLACEHOLDER_ID
    )
    return render_template(
        "trade_market.html",
        membership=mem,
        my_team=my_team,
        admin_team_options=admin_team_options,
        admin_team_id=admin_team_id,
        active_team_id=my_team_id,
        is_site_admin=is_site_admin,
        admin_can_act=mem is not None or is_site_admin,
        can_show_gm_names=current_user.is_authenticated,
        can_message_gms=mem is not None,
        selling_rows=selling_rows,
        buying_rows=buying_rows,
        market_team_rows=market_team_rows,
        my_listings=my_listings,
        my_buying=my_buying,
        buying_categories=BUYING_CATEGORIES,
        sort_key=sort_key,
        sort_order=sort_order,
        gm_display_name=gm_display_name,
        player_page_url_template=player_page_url_template,
        activity_ticker=activity_ticker,
    )


@site_gm_bp.get("/trade-market/assets")
@login_required
def trade_market_assets():
    slug = _league_slug()
    mem = _membership()
    admin_team_id = _admin_trade_team_id() if mem is None else None
    if not mem and not (_is_site_admin() and admin_team_id):
        abort(404)
    team_id = int(mem.team_id) if mem else int(admin_team_id)
    raw_dir = _trade_tool_raw_dir()
    completed_panels = complete_stale_draft_pick_ownership_panels(
        db.session,
        db.session,
        league_slug=slug,
    )
    if completed_panels:
        commit_with_sqlite_retry(db.session)
    assets = selectable_selling_assets(
        db.session,
        db.session,
        league_slug=slug,
        team_id=int(team_id),
        raw_dir=raw_dir,
    )
    _finalize_trade_asset_side_urls(assets)
    return jsonify({"assets": assets})


@site_gm_bp.post("/trade-market/selling")
@login_required
def trade_market_selling_save():
    from flask_wtf.csrf import validate_csrf

    slug = _league_slug()
    mem = _membership()
    data = request.get_json(silent=True) or {}
    admin_team_id = None
    if mem is None and _is_site_admin():
        try:
            admin_team_id = int(data.get("admin_team_id") or 0)
        except (TypeError, ValueError):
            admin_team_id = 0
    if not mem and not (_is_site_admin() and admin_team_id):
        return jsonify({"error": "No active GM membership for this league."}), 403
    left_team_id = int(mem.team_id) if mem else int(admin_team_id)
    try:
        validate_csrf(data.get("csrf_token"))
    except Exception:
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    items = data.get("items")
    if not isinstance(items, list):
        return jsonify({"error": "items array required"}), 400
    old_rows = list(
        db.session.scalars(
            select(TradeMarketListing).where(
                TradeMarketListing.league_slug == slug,
                TradeMarketListing.team_id == int(left_team_id),
            )
        ).all()
    )
    prev_hash = _trade_market_prev_discord_hash(old_rows)
    raw_dir = _trade_tool_raw_dir()
    rows, err = replace_selling_listings(
        db.session,
        db.session,
        league_slug=slug,
        user_id=int(current_user.id),
        team_id=int(left_team_id),
        items=items,
        raw_dir=raw_dir,
    )
    if err:
        db.session.rollback()
        return jsonify({"error": err}), 400
    my_team = db.session.get(Team, int(left_team_id))
    tf = team_fields_for_discord(my_team) if my_team else {}
    if selling_discord_update_should_enqueue(old_rows, rows):
        maybe_enqueue_selling_discord(
            db.session,
            db.session,
            league_slug=slug,
            team_id=int(left_team_id),
            listings=rows,
            team_fields=tf,
            previous_hash=prev_hash,
        )
    commit_with_sqlite_retry(db.session)
    return jsonify({"ok": True, "count": len(rows)})


@site_gm_bp.post("/trade-market/buying")
@login_required
def trade_market_buying_save():
    from flask_wtf.csrf import validate_csrf

    slug = _league_slug()
    mem = _membership()
    data = request.get_json(silent=True) or {}
    admin_team_id = None
    if mem is None and _is_site_admin():
        try:
            admin_team_id = int(data.get("admin_team_id") or 0)
        except (TypeError, ValueError):
            admin_team_id = 0
    if not mem and not (_is_site_admin() and admin_team_id):
        return jsonify({"error": "No active GM membership for this league."}), 403
    left_team_id = int(mem.team_id) if mem else int(admin_team_id)
    try:
        validate_csrf(data.get("csrf_token"))
    except Exception:
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    cats = data.get("categories")
    if not isinstance(cats, list):
        return jsonify({"error": "categories array required"}), 400
    note = str(data.get("note") or "").strip()
    old_rows = list(
        db.session.scalars(
            select(TradeMarketBuyingNeed).where(
                TradeMarketBuyingNeed.league_slug == slug,
                TradeMarketBuyingNeed.team_id == int(left_team_id),
            )
        ).all()
    )
    prev_hash = _trade_market_prev_discord_hash(old_rows)
    needs = replace_buying_needs(
        db.session,
        league_slug=slug,
        user_id=int(current_user.id),
        team_id=int(left_team_id),
        categories=cats,
        note=note,
    )
    my_team = db.session.get(Team, int(left_team_id))
    tf = team_fields_for_discord(my_team) if my_team else {}
    if buying_discord_update_should_enqueue(old_rows, needs):
        maybe_enqueue_buying_discord(
            db.session,
            league_slug=slug,
            team_id=int(left_team_id),
            needs=needs,
            team_fields=tf,
            previous_hash=prev_hash,
        )
    commit_with_sqlite_retry(db.session)
    return jsonify({"ok": True, "count": len(needs)})


@site_gm_bp.post("/trade-market/chat")
@login_required
def trade_market_chat_start():
    from flask_wtf.csrf import validate_csrf

    slug = _league_slug()
    mem = _membership()
    if mem is None:
        return jsonify({"error": "No active GM membership for this league."}), 403
    data = request.get_json(silent=True) or {}
    try:
        validate_csrf(data.get("csrf_token"))
    except Exception:
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    try:
        peer_user_id = int(data.get("peer_user_id") or 0)
    except (TypeError, ValueError):
        peer_user_id = 0
    if not peer_user_id or peer_user_id == int(current_user.id):
        return jsonify({"error": "Choose another GM to chat with."}), 400
    peer_mem = active_peer_membership(slug, peer_user_id)
    if not peer_mem:
        return jsonify({"error": "That GM is not active in this league."}), 404
    context = str(data.get("context") or "").strip()
    kind = str(data.get("kind") or "Trade Market").strip() or "Trade Market"
    body = str(data.get("body") or "").strip()
    if not body:
        return jsonify({"error": "Message cannot be empty."}), 400
    if len(body) > _GM_MESSAGE_MAX_LEN:
        return jsonify({"error": f"Message is too long (max {_GM_MESSAGE_MAX_LEN} characters)."}), 400
    prefix = f"Trade Market chat ({kind})"
    if context:
        prefix += f": {context[:500]}"
    msg_body = f"{prefix}\n\n{body}"
    create_gm_message(
        league_slug=slug,
        from_user_id=int(current_user.id),
        to_user_id=peer_user_id,
        body=msg_body[:_GM_MESSAGE_MAX_LEN],
        event_key="trade_market_chat",
    )
    commit_with_sqlite_retry(db.session)
    return jsonify(
        {
            "ok": True,
            "thread_url": url_for("site_gm.gm_messages_thread", peer_user_id=peer_user_id),
        }
    )


def _ai_trade_draft_round_cap(session, league_slug: str) -> int:
    return min(8, int(trade_tool_draft_round_cap(session, league_slug)))


@site_gm_bp.route("/ai-trade-tool", methods=["GET"])
@login_required
def ai_trade_tool():
    """Hypothetical trade + entertainment AI opinion (not submitted for approval)."""
    slug = _league_slug()
    mem = _membership()
    if not _trade_page_allowed(mem):
        flash("No active GM membership for this league.", "err")
        return redirect(url_for("main.home"))
    admin_team_id = _admin_trade_team_id() if mem is None else None
    my_team_id = int(mem.team_id) if mem else admin_team_id
    my_team = db.session.get(Team, int(my_team_id)) if my_team_id else None
    if mem is None and _is_site_admin():
        partner_options = _trade_team_options(exclude_team_id=my_team_id)
        admin_team_options = _trade_team_options()
    else:
        partner_options = _trade_partner_options(
            slug,
            exclude_user_id=int(current_user.id),
            exclude_team_id=my_team_id,
        )
        admin_team_options = []
    my_team_logo_url = team_logo_url_for_team(my_team) if my_team else ""
    draft_round_cap = _ai_trade_draft_round_cap(db.session, slug)
    player_page_url_template = url_for("main.player_page", player_id=_TRADE_PLAYER_URL_PLACEHOLDER_ID)
    recent_trade_rows = build_trade_log_rows(
        db.session, db.session, league_slug=slug, limit=15
    )
    return render_template(
        "ai_trade_tool.html",
        membership=mem,
        my_team=my_team,
        my_team_logo_url=my_team_logo_url,
        partner_options=partner_options,
        admin_team_options=admin_team_options,
        admin_team_id=admin_team_id,
        admin_read_only=mem is None and _is_site_admin(),
        gm_display_name=gm_display_name,
        draft_round_cap=draft_round_cap,
        player_page_url_template=player_page_url_template,
        recent_trade_rows=recent_trade_rows,
    )


@site_gm_bp.post("/operations/ai-trade-tool/evaluate")
@login_required
def ai_trade_tool_evaluate():
    from flask_wtf.csrf import validate_csrf

    slug = _league_slug()
    mem = _membership()
    if not mem:
        return jsonify({"error": "No active GM membership for this league."}), 403
    left_team_id = int(mem.team_id)
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
            GmLeagueMembership.team_id != int(left_team_id),
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
        int(left_team_id),
        int(partner_team_id),
        left_out,
        right_out,
        raw_dir=_trade_tool_raw_dir(),
        league_slug=slug,
        draft_round_cap=cap,
    )
    if err:
        return jsonify({"error": err}), 400
    from_team = db.session.get(Team, int(left_team_id))
    to_team = db.session.get(Team, int(partner_team_id))
    recent_ctx = recent_trades_prompt_block(
        build_trade_log_rows(db.session, db.session, league_slug=slug, limit=12)
    )
    out = fetch_trade_ai_opinion(
        db.session,
        user_id=int(current_user.id),
        from_team=from_team,
        to_team=to_team,
        left=left_out,
        right=right_out,
        notes=notes,
        league_slug=slug,
        recent_trades_context=recent_ctx,
    )
    if out.get("error"):
        return jsonify({"error": out["error"], "details": out.get("details") or ""}), 503
    return jsonify(out)


@site_gm_bp.post("/operations/trade-log/ai-take")
@login_required
def trade_log_ai_take():
    """Entertainment AI opinion on an existing trade-log row."""
    from flask_wtf.csrf import validate_csrf

    if not _trade_page_allowed(_membership()):
        return jsonify({"error": "Trade log AI is for active GMs and league admins."}), 403
    data = request.get_json(silent=True) or {}
    try:
        validate_csrf(data.get("csrf_token"))
    except Exception:
        return jsonify({"error": "Invalid or missing CSRF token."}), 400
    source = str(data.get("source") or "").strip().lower()
    try:
        row_id = int(data.get("id"))
    except (TypeError, ValueError):
        return jsonify({"error": "id required"}), 400
    if source not in ("manual", "csv", "site") or row_id <= 0:
        return jsonify({"error": "Invalid trade log reference."}), 400
    slug = _league_slug()
    row = resolve_trade_log_row(db.session, db.session, league_slug=slug, source=source, row_id=row_id)
    if not row:
        return jsonify({"error": "Trade not found."}), 404
    out = fetch_logged_trade_ai_opinion(user_id=int(current_user.id), row=row)
    if out.get("error"):
        return jsonify({"error": out["error"], "details": out.get("details") or ""}), 503
    return jsonify(out)


def _parse_trade_log_date(raw: str | None) -> date | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _admin_trade_log_team_options() -> list[Team]:
    return list(db.session.scalars(select(Team).order_by(Team.name)).all())


def _admin_trade_log_team_option_groups() -> tuple[list[dict[str, str]], list[dict[str, str]], list[Team]]:
    csv_path = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)) / "team_identity_history.csv"
    if sync_franchise_identities_from_csv_if_needed(db.session, csv_path) is not None:
        commit_with_sqlite_retry(db.session)
    teams = _admin_trade_log_team_options()
    teams_by_fhm: dict[str, Team] = {}
    for t in teams:
        fhm = norm_fhm_team_id(t.fhm_team_id)
        if fhm is not None:
            teams_by_fhm[fhm] = t
    logo_bundle = get_season_team_logo_bundle()

    def _identity_option_logo(ident: FranchiseTeamIdentity, team: Team) -> str:
        if ident.logo_file:
            hit = identity_logo_url(ident.logo_file)
            if hit:
                return hit
        return logo_bundle.team_logo_url_for_season_context(team, int(ident.start_year))

    defunct_options: list[dict[str, str]] = []
    rows = db.session.scalars(
        select(FranchiseTeamIdentity)
        .options(joinedload(FranchiseTeamIdentity.team))
        .where(FranchiseTeamIdentity.end_year.is_not(None))
        .order_by(
            FranchiseTeamIdentity.display_name.asc(),
            FranchiseTeamIdentity.start_year.asc(),
            FranchiseTeamIdentity.id.asc(),
        )
    ).all()
    for ident in rows:
        fhm = norm_fhm_team_id(ident.team_fhm_id)
        team = ident.team or (teams_by_fhm.get(fhm) if fhm is not None else None)
        if team is None:
            continue
        years = f"{ident.start_year}-{ident.end_year}" if ident.end_year else str(ident.start_year)
        label = f"{ident.display_name} ({years})"
        defunct_options.append(
            {
                "value": f"identity:{int(ident.id)}",
                "label": label,
                "display_name": ident.display_name,
                "team_id": str(int(team.id)),
                "logo_url": _identity_option_logo(ident, team),
            }
        )
    active_options = [
        {
            "value": f"team:{int(t.id)}",
            "label": t.full_display_name(),
            "display_name": t.full_display_name(),
            "team_id": str(int(t.id)),
            "logo_url": team_logo_url_for_team(t),
        }
        for t in teams
    ]
    return defunct_options, active_options, teams


def _resolve_admin_trade_log_team_choice(raw: str | None) -> tuple[int, str] | None:
    value = str(raw or "").strip()
    if not value:
        return None
    if value.startswith("identity:"):
        try:
            rid = int(value.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
        ident = db.session.get(FranchiseTeamIdentity, rid)
        if ident is None:
            return None
        team = ident.team
        if team is None and ident.team_fhm_id:
            team = db.session.scalar(select(Team).where(Team.fhm_team_id == str(ident.team_fhm_id)).limit(1))
        if team is None:
            return None
        years = f"{ident.start_year}-{ident.end_year}" if ident.end_year else str(ident.start_year)
        label = f"{ident.display_name} ({years})" if ident.display_name else team.full_display_name()
        return int(team.id), label.strip()
    if value.startswith("team:"):
        value = value.split(":", 1)[1]
    try:
        tid = int(value)
    except (TypeError, ValueError):
        return None
    team = db.session.get(Team, tid)
    if team is None:
        return None
    return int(team.id), team.full_display_name()


def _manual_trade_summary_from_parts(
    *,
    team_a_label: str,
    team_b_label: str,
    team_a_outgoing: str,
    team_b_outgoing: str,
) -> str:
    """Store split manual trade details in the existing summary field."""
    a_label = (team_a_label or "Team A").strip() or "Team A"
    b_label = (team_b_label or "Team B").strip() or "Team B"
    a_body = (team_a_outgoing or "").strip()
    b_body = (team_b_outgoing or "").strip()
    return f"{a_label} sends:\n{a_body}\n\n{b_label} sends:\n{b_body}"


def _manual_trade_summary_blocks(summary: str | None) -> tuple[tuple[str, str], tuple[str, str]]:
    """Split structured manual trade summaries into stored headings and bodies."""
    text = (summary or "").strip()
    if not text:
        return ("", ""), ("", "")
    blocks = text.split("\n\n", 1)
    if len(blocks) != 2:
        return ("", text), ("", "")

    def _heading_and_body(block: str) -> tuple[str, str]:
        lines = block.splitlines()
        if len(lines) >= 2 and lines[0].strip().lower().endswith(" sends:"):
            label = lines[0].strip()[: -len(" sends:")].strip()
            return label, "\n".join(lines[1:]).strip()
        return "", block.strip()

    return _heading_and_body(blocks[0]), _heading_and_body(blocks[1])


def _manual_trade_summary_parts(summary: str | None) -> tuple[str, str]:
    """Split structured manual trade summaries back into Team A / Team B fields."""
    a_block, b_block = _manual_trade_summary_blocks(summary)
    return a_block[1], b_block[1]


def _manual_trade_asset_rows(outgoing: str | None) -> list[dict[str, str]]:
    """Split stored outgoing assets into editable rows for the admin form."""
    lines = [ln.strip() for ln in (outgoing or "").splitlines() if ln.strip()]
    if not lines:
        return [{"player": "", "other": ""}]
    return [{"player": ln, "other": ""} for ln in lines]


def _manual_trade_outgoing_from_form(form, prefix: str) -> str:
    """Combine per-row player and other asset fields into one outgoing block."""
    players = form.getlist(f"{prefix}_player[]")
    others = form.getlist(f"{prefix}_other[]")
    count = max(len(players), len(others))
    lines: list[str] = []
    for i in range(count):
        player = (players[i] if i < len(players) else "").strip()
        other = (others[i] if i < len(others) else "").strip()
        if player:
            lines.append(player)
        if other:
            lines.append(other)
    return "\n".join(lines)


def _manual_trade_summary_labels(summary: str | None) -> tuple[str, str]:
    a_block, b_block = _manual_trade_summary_blocks(summary)
    return a_block[0], b_block[0]


def _manual_trade_identity_from_label(label: str, team: Team | None) -> FranchiseTeamIdentity | None:
    raw = (label or "").strip()
    if not raw:
        return None
    display_name = raw
    start_year = end_year = None
    if raw.endswith(")") and "(" in raw:
        display_name, years_raw = raw.rsplit("(", 1)
        display_name = display_name.strip()
        years = years_raw[:-1].strip()
        if "-" in years:
            first, second = years.split("-", 1)
            try:
                start_year = int(first.strip())
            except (TypeError, ValueError):
                start_year = None
            try:
                end_year = int(second.strip())
            except (TypeError, ValueError):
                end_year = None
        else:
            try:
                start_year = int(years)
            except (TypeError, ValueError):
                start_year = None
    predicates = [FranchiseTeamIdentity.display_name == display_name]
    if team is not None:
        team_clauses = [FranchiseTeamIdentity.team_id == int(team.id)]
        fhm = str(team.fhm_team_id or "").strip()
        if fhm:
            team_clauses.append(FranchiseTeamIdentity.team_fhm_id == fhm)
        predicates.append(or_(*team_clauses))
    if start_year is not None:
        predicates.append(FranchiseTeamIdentity.start_year == start_year)
    if end_year is not None:
        predicates.append(FranchiseTeamIdentity.end_year == end_year)

    return db.session.scalar(
        select(FranchiseTeamIdentity)
        .where(*predicates)
        .order_by(
            FranchiseTeamIdentity.start_year.desc(),
            FranchiseTeamIdentity.id.desc(),
        )
        .limit(1)
    )


def _manual_trade_team_view(_entry: TradeLogEntry, team: Team | None, label: str, logo_bundle=None) -> dict[str, str]:
    display = (label or "").strip() or (team.full_display_name() if team else "Team")
    if logo_bundle is None:
        logo_bundle = get_season_team_logo_bundle()
    logo_url = ""
    ident = _manual_trade_identity_from_label(display, team)
    if ident is not None and ident.logo_file:
        logo_url = identity_logo_url(ident.logo_file) or ""
    if not logo_url and ident is not None and team is not None:
        logo_url = logo_bundle.team_logo_url_for_season_context(team, int(ident.start_year))
    if not logo_url and team is not None:
        logo_url = team_logo_url_for_team(team)
    return {"label": display, "logo_url": logo_url}


def _manual_trade_selected_value(
    entry: TradeLogEntry,
    side: str,
    defunct_options: list[dict[str, str]],
) -> str:
    team_id = int(entry.team_a_id if side == "a" else entry.team_b_id)
    labels = _manual_trade_summary_labels(entry.summary)
    label = labels[0] if side == "a" else labels[1]
    ident = _manual_trade_identity_from_label(label, db.session.get(Team, team_id))
    if ident is not None:
        return f"identity:{int(ident.id)}"
    normalized = " ".join((label or "").lower().split())
    if normalized:
        for opt in defunct_options:
            if opt.get("team_id") == str(team_id):
                opt_names = {
                    " ".join(str(opt.get("label") or "").lower().split()),
                    " ".join(str(opt.get("display_name") or "").lower().split()),
                }
                if normalized in opt_names:
                    return str(opt.get("value") or "")
    return f"team:{team_id}"


def _manual_trade_admin_row_views(rows: list[TradeLogEntry], teams_by_id: dict[int, Team]) -> list[dict]:
    """Precompute display fields so the admin listing stays cheap to render."""
    logo_bundle = get_season_team_logo_bundle()
    team_view_cache: dict[tuple[int, str], dict[str, str]] = {}
    out: list[dict] = []
    for ent in rows:
        ta = teams_by_id.get(int(ent.team_a_id))
        tb = teams_by_id.get(int(ent.team_b_id))
        parts = _manual_trade_summary_parts(ent.summary)
        labels = _manual_trade_summary_labels(ent.summary)

        def cached_view(team: Team | None, label: str) -> dict[str, str]:
            team_id = int(team.id) if team is not None else 0
            key = (team_id, (label or "").strip())
            hit = team_view_cache.get(key)
            if hit is not None:
                return hit
            hit = _manual_trade_team_view(ent, team, label, logo_bundle)
            team_view_cache[key] = hit
            return hit

        out.append(
            {
                "entry": ent,
                "trade_date": ent.trade_date.strftime("%Y-%m-%d") if ent.trade_date else "—",
                "team_a": cached_view(ta, labels[0]),
                "team_b": cached_view(tb, labels[1]),
                "team_a_sent": parts[0],
                "team_b_sent": parts[1],
            }
        )
    return out


@site_admin_bp.route("/trade-log", methods=["GET", "POST"])
@login_required
def admin_trade_log():
    """Manual historical trade log entries (pre–Trade Tool workflow)."""
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    defunct_team_options, active_team_options, teams = _admin_trade_log_team_option_groups()
    if request.method == "POST":
        action = (request.form.get("action") or "create").strip().lower()
        if action == "create":
            team_a_choice = _resolve_admin_trade_log_team_choice(request.form.get("team_a_id"))
            team_b_choice = _resolve_admin_trade_log_team_choice(request.form.get("team_b_id"))
            team_a_id = team_a_choice[0] if team_a_choice else 0
            team_b_id = team_b_choice[0] if team_b_choice else 0
            team_a_outgoing = _manual_trade_outgoing_from_form(request.form, "team_a")
            team_b_outgoing = _manual_trade_outgoing_from_form(request.form, "team_b")
            trade_d = _parse_trade_log_date(request.form.get("trade_date"))
            if not team_a_id or not team_b_id or team_a_id == team_b_id:
                flash("Choose two different teams.", "err")
            elif not team_a_outgoing or not team_b_outgoing:
                flash("Enter what left both teams.", "err")
            else:
                summary = _manual_trade_summary_from_parts(
                    team_a_label=team_a_choice[1] if team_a_choice else "Team A",
                    team_b_label=team_b_choice[1] if team_b_choice else "Team B",
                    team_a_outgoing=team_a_outgoing,
                    team_b_outgoing=team_b_outgoing,
                )
                ent = TradeLogEntry(
                    trade_date=trade_d,
                    team_a_id=team_a_id,
                    team_b_id=team_b_id,
                    summary=summary[:8000],
                    source="manual",
                    external_id=None,
                )
                db.session.add(ent)
                commit_with_sqlite_retry(db.session)
                flash("Manual trade log entry added.", "ok")
                return redirect(url_for("site_admin.admin_trade_log"))
        return redirect(url_for("site_admin.admin_trade_log"))

    per_page = 50
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    manual_total = int(
        db.session.scalar(
            select(func.count(TradeLogEntry.id)).where(TradeLogEntry.source == "manual")
        )
        or 0
    )
    manual_pages = max(1, (manual_total + per_page - 1) // per_page)
    page = min(page, manual_pages)
    manual_rows = list(
        db.session.scalars(
            select(TradeLogEntry)
            .where(TradeLogEntry.source == "manual")
            .order_by(TradeLogEntry.trade_date.desc().nulls_last(), TradeLogEntry.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()
    )
    teams_by_id = {int(t.id): t for t in teams}
    manual_row_views = _manual_trade_admin_row_views(manual_rows, teams_by_id)
    return render_template(
        "admin_trade_log.html",
        manual_row_views=manual_row_views,
        manual_page=page,
        manual_pages=manual_pages,
        manual_total=manual_total,
        teams=teams,
        defunct_team_options=defunct_team_options,
        active_team_options=active_team_options,
        player_search_url=url_for("api.search_players"),
    )


@site_admin_bp.route("/trade-log/<int:entry_id>/edit", methods=["GET", "POST"])
@login_required
def admin_trade_log_edit(entry_id: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    ent = db.session.get(TradeLogEntry, entry_id)
    if not ent or (ent.source or "").strip().lower() != "manual":
        abort(404)
    defunct_team_options, active_team_options, teams = _admin_trade_log_team_option_groups()
    if request.method == "POST":
        team_a_choice = _resolve_admin_trade_log_team_choice(request.form.get("team_a_id"))
        team_b_choice = _resolve_admin_trade_log_team_choice(request.form.get("team_b_id"))
        team_a_id = team_a_choice[0] if team_a_choice else 0
        team_b_id = team_b_choice[0] if team_b_choice else 0
        team_a_outgoing = _manual_trade_outgoing_from_form(request.form, "team_a")
        team_b_outgoing = _manual_trade_outgoing_from_form(request.form, "team_b")
        trade_d = _parse_trade_log_date(request.form.get("trade_date"))
        if not team_a_id or not team_b_id or team_a_id == team_b_id:
            flash("Choose two different teams.", "err")
        elif not team_a_outgoing or not team_b_outgoing:
            flash("Enter what left both teams.", "err")
        else:
            summary = _manual_trade_summary_from_parts(
                team_a_label=team_a_choice[1] if team_a_choice else "Team A",
                team_b_label=team_b_choice[1] if team_b_choice else "Team B",
                team_a_outgoing=team_a_outgoing,
                team_b_outgoing=team_b_outgoing,
            )
            ent.trade_date = trade_d
            ent.team_a_id = team_a_id
            ent.team_b_id = team_b_id
            ent.summary = summary[:8000]
            commit_with_sqlite_retry(db.session)
            flash("Trade log entry updated.", "ok")
            return redirect(url_for("site_admin.admin_trade_log"))
    summary_parts = _manual_trade_summary_parts(ent.summary)
    return render_template(
        "admin_trade_log_edit.html",
        entry=ent,
        teams=teams,
        defunct_team_options=defunct_team_options,
        active_team_options=active_team_options,
        summary_parts=summary_parts,
        team_a_asset_rows=_manual_trade_asset_rows(summary_parts[0]),
        team_b_asset_rows=_manual_trade_asset_rows(summary_parts[1]),
        selected_team_a_value=_manual_trade_selected_value(ent, "a", defunct_team_options),
        selected_team_b_value=_manual_trade_selected_value(ent, "b", defunct_team_options),
        player_search_url=url_for("api.search_players"),
    )


@site_admin_bp.post("/trade-log/<int:entry_id>/delete")
@login_required
def admin_trade_log_delete(entry_id: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    ent = db.session.get(TradeLogEntry, entry_id)
    if not ent or (ent.source or "").strip().lower() != "manual":
        abort(404)
    db.session.delete(ent)
    commit_with_sqlite_retry(db.session)
    flash("Manual trade log entry deleted.", "ok")
    return redirect(url_for("site_admin.admin_trade_log"))


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
    """Serialize teams for the admin draft lottery UI (BOWL-Relegation only)."""
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


def _boost_lottery_team_rows(league_slug: str) -> list[dict[str, object]]:
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    result_rows = db.session.scalars(
        select(BoostLotteryTeamResult).where(BoostLotteryTeamResult.league_slug == league_slug)
    ).all()
    by_team = {int(r.team_id): r for r in result_rows}
    rows: list[dict[str, object]] = []
    for team in teams:
        stored = by_team.get(int(team.id))
        gold = int(getattr(stored, "gold_count", 0) or 0)
        silver = int(getattr(stored, "silver_count", 0) or 0)
        rows.append(
            {
                "team_id": int(team.id),
                "name": team.full_display_name(),
                "abbr": str(team.abbreviation or "")[:8],
                "logo_url": team_logo_url_for_team(team),
                "gold_count": gold,
                "silver_count": silver,
                "total_count": gold + silver,
            }
        )
    rows.sort(key=lambda r: (-int(r["total_count"]), -int(r["gold_count"]), str(r["name"]).lower()))
    return rows


def _save_boost_lottery_team_rows(league_slug: str, user_id: int) -> int:
    teams = db.session.scalars(select(Team).order_by(Team.id)).all()
    existing = {
        int(r.team_id): r
        for r in db.session.scalars(
            select(BoostLotteryTeamResult).where(BoostLotteryTeamResult.league_slug == league_slug)
        ).all()
    }
    changed = 0
    now = datetime.utcnow()
    for team in teams:
        tid = int(team.id)
        try:
            gold = max(0, int(request.form.get(f"gold_{tid}") or "0"))
        except ValueError:
            gold = 0
        try:
            silver = max(0, int(request.form.get(f"silver_{tid}") or "0"))
        except ValueError:
            silver = 0
        row = existing.get(tid)
        if row is None and (gold or silver):
            row = BoostLotteryTeamResult(
                league_slug=league_slug,
                team_id=tid,
                gold_count=gold,
                silver_count=silver,
                updated_by_user_id=user_id,
                updated_at=now,
            )
            db.session.add(row)
            changed += 1
        elif row is not None and (int(row.gold_count or 0), int(row.silver_count or 0)) != (gold, silver):
            row.gold_count = gold
            row.silver_count = silver
            row.updated_by_user_id = user_id
            row.updated_at = now
            changed += 1
    commit_with_sqlite_retry(db.session)
    return changed


def _boost_lottery_theme(league_slug: str) -> str:
    return "fantasy" if league_slug == "bowl-fantasy" else ("cap" if league_slug == "bowl-cap" else "historical")


@site_gm_bp.route("/draft-lottery", methods=["GET"])
@login_required
def draft_lottery():
    """Weighted 8-slot draft lottery sim (BOWL-Relegation site admins only)."""
    slug = _league_slug()
    if slug != "bowl-fantasy":
        abort(404)
    if not getattr(current_user, "is_admin", False):
        flash("Draft lottery is only available to league admins.", "err")
        return redirect(url_for("main.home"))
    team_rows = _draft_lottery_team_rows()
    return render_template("draft_lottery.html", team_rows=team_rows)


@site_gm_bp.route("/boost-lottery", methods=["GET", "POST"])
@login_required
def boost_lottery():
    """Draft boost ticket lottery (Relegation / Cap / Historical site admins)."""
    slug = _league_slug()
    if slug not in ("bowl-fantasy", "bowl-cap", "bowl-historical"):
        abort(404)
    if not getattr(current_user, "is_admin", False):
        flash("Boost lottery is only available to league admins.", "err")
        return redirect(url_for("main.home"))
    if request.method == "POST":
        changed = _save_boost_lottery_team_rows(slug, int(current_user.id))
        flash(f"Boost Lottery winner tracker saved ({changed} team row{'s' if changed != 1 else ''} changed).", "ok")
        return redirect(url_for("site_gm.boost_lottery"))
    return render_template(
        "boost_lottery.html",
        boost_theme=_boost_lottery_theme(slug),
        boost_team_rows=_boost_lottery_team_rows(slug),
    )


@site_gm_bp.get("/boost-lottery-tracker")
@login_required
def boost_lottery_tracker():
    """Read-only boost winner totals for active GMs."""
    slug = _league_slug()
    if slug not in ("bowl-fantasy", "bowl-cap", "bowl-historical"):
        abort(404)
    mem = _membership()
    if not mem:
        flash("Boost Lottery Tracker is available to active GMs.", "err")
        return redirect(url_for("main.home"))
    return render_template(
        "boost_lottery_tracker.html",
        boost_theme=_boost_lottery_theme(slug),
        boost_team_rows=_boost_lottery_team_rows(slug),
        membership=mem,
    )


@site_gm_bp.route("/staff-salaries", methods=["GET", "POST"])
@login_required
def staff_salaries_page():
    """League office Staff Hire/Fire — admins only (Trade Tool pattern)."""
    if not _can_use_official_staff_tool():
        flash("Staff Hire/Fire is available to league administrators only.", "err")
        return redirect(url_for("main.home"))
    slug = _league_slug()
    base = staff_salary_context(db.session, league_slug=slug)
    start_year = base.get("season_start_year")
    admin_team_id = _admin_staff_team_id()

    if start_year is not None:
        expire_stale_staff_contracts(
            db.session,
            league_slug=slug,
            season_start_year=int(start_year),
        )

    if request.method == "POST":
        if start_year is None:
            flash("Staff actions are unavailable until season budgets are configured.", "err")
            return redirect(url_for("site_gm.staff_salaries_page"))
        if not admin_team_id:
            flash("Choose a team before hiring or firing staff.", "err")
            return redirect(url_for("site_gm.staff_salaries_page"))
        return_url = url_for("site_gm.staff_salaries_page", admin_team_id=admin_team_id)
        action = (request.form.get("action") or "").strip()
        if action == "hire":
            try:
                contract_years = int(request.form.get("contract_years") or "1")
            except ValueError:
                contract_years = 1
            result = admin_hire_staff(
                db.session,
                league_slug=slug,
                season_start_year=int(start_year),
                team_id=int(admin_team_id),
                admin_user_id=int(current_user.id),
                staff_fhm_id=(request.form.get("staff_fhm_id") or "").strip(),
                role=(request.form.get("role") or "").strip(),
                contract_years=contract_years,
            )
            if result.ok and result.entry:
                team = db.session.get(Team, int(admin_team_id))
                _publish_admin_staff_transaction(
                    slug=slug, team=team, entry=result.entry, action="hire"
                )
        elif action == "fire":
            try:
                penalty_amount = int(
                    str(request.form.get("penalty_amount") or "0")
                    .replace(",", "")
                    .replace("$", "")
                )
            except ValueError:
                penalty_amount = 0
            result = admin_fire_staff(
                db.session,
                league_slug=slug,
                season_start_year=int(start_year),
                team_id=int(admin_team_id),
                admin_user_id=int(current_user.id),
                staff_fhm_id=(request.form.get("staff_fhm_id") or "").strip(),
                penalty_amount=penalty_amount,
            )
            if result.ok and result.entry:
                team = db.session.get(Team, int(admin_team_id))
                _publish_admin_staff_transaction(
                    slug=slug, team=team, entry=result.entry, action="fire"
                )
        else:
            flash("Unknown action.", "err")
            return redirect(return_url)
        commit_with_sqlite_retry(db.session)
        flash(result.message, "ok" if result.ok else "err")
        return redirect(return_url)

    admin_team = db.session.get(Team, int(admin_team_id)) if admin_team_id else None
    ctx = dict(base)
    ctx["admin_team_id"] = admin_team_id
    ctx["admin_team"] = admin_team
    ctx["admin_team_options"] = _trade_team_options()
    ctx["staff_search_url"] = url_for("api.search_staff")
    ctx["staff_placeholder_url"] = staff_placeholder_url()
    if admin_team_id and start_year is not None:
        from app.services.league_finances import staff_finances_for_team

        ctx["gm_staff_finances"] = staff_finances_for_team(
            db.session,
            league_slug=slug,
            team_id=int(admin_team_id),
            season_start_year=int(start_year),
            defaults=ctx.get("defaults"),
        )
    else:
        ctx["gm_staff_finances"] = None
    return render_template("staff_salaries.html", **ctx)


@site_gm_bp.get("/finances")
@login_required
def finances_page():
    """League-wide player and staff finances (GMs and admins)."""
    slug = _league_slug()
    if not _membership() and not _is_site_admin():
        flash("Finances is available to active GMs and league admins.", "err")
        return redirect(url_for("main.home"))
    from app.services.staff_salaries import current_season_start_year
    from app.services.staff_transactions import expire_stale_staff_contracts

    start_year = current_season_start_year(db.session)
    if start_year is not None:
        expire_stale_staff_contracts(
            db.session,
            league_slug=slug,
            season_start_year=int(start_year),
        )
        commit_with_sqlite_retry(db.session)
    ctx = build_league_finances_context(
        db.session,
        league_slug=slug,
        raw_import_dir=Path(current_app.config["RAW_IMPORT_DIR"]),
    )
    ctx["membership"] = _membership()
    return render_template("finances.html", **ctx)


@site_gm_bp.route("/rfa-offers", methods=["GET", "POST"])
@login_required
def rfa_offers_page():
    """Restricted free agent offer sheets (GMs and league admins)."""
    slug = _league_slug()
    mem = _membership()
    is_admin = bool(getattr(current_user, "is_admin", False))
    if not mem and not is_admin:
        flash("RFA Offers is available to active GMs and league admins.", "err")
        return redirect(url_for("main.home"))
    if request.method == "POST":
        if not mem:
            flash("Submitting offer sheets requires an active GM membership.", "err")
            return redirect(url_for("site_gm.rfa_offers_page"))
        try:
            player_id = int(request.form.get("player_id") or "0")
            offer_salary = int(str(request.form.get("offer_salary") or "0").replace(",", "").replace("$", ""))
            offer_years = int(request.form.get("offer_years") or "0")
        except ValueError:
            flash("Invalid offer amount or term.", "err")
            return redirect(url_for("site_gm.rfa_offers_page"))
        special_clauses = (request.form.get("special_clauses") or "").strip()
        candidate, comp, err = validate_offer_submission(
            db.session,
            db.session,
            league_slug=slug,
            offering_team_id=int(mem.team_id),
            player_id=player_id,
            offer_salary=offer_salary,
            offer_years=offer_years,
        )
        if err or candidate is None or comp is None:
            flash(err or "Unable to submit offer.", "err")
            return redirect(url_for("site_gm.rfa_offers_page"))
        open_req = db.session.scalar(
            select(RfaOfferRequest.id)
            .where(
                RfaOfferRequest.league_slug == slug,
                RfaOfferRequest.player_id == int(player_id),
                RfaOfferRequest.offering_team_id == int(mem.team_id),
                RfaOfferRequest.status.in_(
                    ("pending_admin", "awaiting_equalization", "awaiting_original_match")
                ),
            )
            .limit(1)
        )
        if open_req:
            flash("You already have an open offer sheet for this player.", "warn")
            return redirect(url_for("site_gm.rfa_offers_page"))
        req = create_rfa_offer_request(
            db.session,
            league_slug=slug,
            offering_user_id=int(current_user.id),
            offering_team_id=int(mem.team_id),
            candidate=candidate,
            offer_salary=offer_salary,
            offer_years=offer_years,
            special_clauses=special_clauses,
            comp=comp,
        )
        try:
            from app.services.admin_review_notify import notify_rfa_offer_pending

            gm_team = db.session.get(Team, int(mem.team_id))
            notify_rfa_offer_pending(
                league_slug=slug,
                league_display_name=league_display_name(slug),
                request_id=int(req.id),
                user_email=str(current_user.email or ""),
                offering_team_id=int(mem.team_id),
                offering_team_name=gm_team.full_display_name() if gm_team else str(mem.team_id),
                player_name=candidate.player.full_name or f"Player #{player_id}",
                offer_salary=offer_salary,
                offer_years=offer_years,
                category_label=CATEGORY_LABELS.get(candidate.category, candidate.category),
            )
        except Exception as exc:
            current_app.logger.warning("Admin notify (RFA offer): %s", exc)
        commit_with_sqlite_retry(db.session)
        flash(f"Offer sheet submitted for {candidate.player.full_name} — pending admin review.", "ok")
        return redirect(url_for("site_gm.rfa_offers_page"))
    candidates = list_rfa_candidates(db.session, league_slug=slug)
    cap_panels_view = build_cap_panels_view(
        db.session,
        db.session,
        league_slug=slug,
        active_count=3,
    )
    season = get_current_season()
    season_start_year = int(season.start_year) if season and season.start_year is not None else None
    current_cap_ceiling = None
    if season_start_year is not None:
        current_cap_ceiling, _ = cap_for_season(db.session, slug, season_start_year)
    comp_reference_rows = compensation_reference_rows(
        int(current_cap_ceiling) if current_cap_ceiling else None
    )
    my_offers: list[RfaOfferRequest] = []
    match_queue: list[RfaOfferRequest] = []
    if mem:
        my_offers = list(
            db.session.scalars(
                select(RfaOfferRequest)
                .where(
                    RfaOfferRequest.league_slug == slug,
                    RfaOfferRequest.offering_team_id == int(mem.team_id),
                )
                .order_by(RfaOfferRequest.created_at.desc())
                .limit(20)
            ).all()
        )
        match_queue = list(
            db.session.scalars(
                select(RfaOfferRequest)
                .where(
                    RfaOfferRequest.league_slug == slug,
                    RfaOfferRequest.rights_team_id == int(mem.team_id),
                    RfaOfferRequest.status == "awaiting_original_match",
                )
                .order_by(RfaOfferRequest.created_at.desc())
            ).all()
        )
    player_ids = {int(r.player_id) for r in my_offers + match_queue}
    players_by_id: dict[int, Player] = {}
    if player_ids:
        players_by_id = {
            int(p.id): p
            for p in db.session.scalars(select(Player).where(Player.id.in_(player_ids))).all()
        }
    team_ids = {int(r.offering_team_id) for r in my_offers + match_queue} | {
        int(r.rights_team_id) for r in my_offers + match_queue
    }
    teams_by_id: dict[int, Team] = {}
    if team_ids:
        teams_by_id = {
            int(t.id): t for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
        }
    return render_template(
        "rfa_offers.html",
        membership=mem,
        gm_team=db.session.get(Team, int(mem.team_id)) if mem else None,
        candidates=candidates,
        category_labels=CATEGORY_LABELS,
        category_tooltips=CATEGORY_TOOLTIPS,
        cap_panels_view=cap_panels_view,
        comp_reference_rows=comp_reference_rows,
        current_cap_ceiling=current_cap_ceiling,
        my_offers=my_offers,
        match_queue=match_queue,
        players_by_id=players_by_id,
        teams_by_id=teams_by_id,
        status_label=status_label,
        can_submit=mem is not None,
    )


@site_gm_bp.get("/rfa-offers/compensation-preview")
@login_required
def rfa_compensation_preview():
    slug = _league_slug()
    mem = _membership()
    if not mem:
        return jsonify({"error": "no_membership"}), 403
    try:
        player_id = int(request.args.get("player_id") or "0")
        offer_salary = int(str(request.args.get("offer_salary") or "0").replace(",", "").replace("$", ""))
    except ValueError:
        return jsonify({"error": "invalid_input"}), 400
    candidate = next(
        (
            r
            for r in list_rfa_candidates(db.session, league_slug=slug, offering_team_id=int(mem.team_id))
            if int(r.player.id) == int(player_id)
        ),
        None,
    )
    if candidate is None:
        return jsonify({"error": "not_eligible"}), 404
    comp = compensation_for_offer(
        db.session,
        db.session,
        league_slug=slug,
        offering_team_id=int(mem.team_id),
        offer_salary=offer_salary,
        category=candidate.category,
    )
    panel = compensation_panel_dict(comp, category=candidate.category)
    panel["minimum_offer"] = int(candidate.minimum_offer)
    panel["submit_disabled"] = not comp.valid or comp.cap_missing
    return jsonify(panel)


@site_gm_bp.route("/rfa-offers/<int:rid>/respond", methods=["GET", "POST"])
@login_required
def rfa_offer_respond(rid: int):
    """Original-team match or reject after player accepts (Groups II–IV)."""
    slug = _league_slug()
    mem = _membership()
    if not mem:
        flash("Match/reject requires an active GM membership.", "err")
        return redirect(url_for("main.home"))
    req = db.session.get(RfaOfferRequest, rid)
    if not req or req.league_slug != slug or int(req.rights_team_id) != int(mem.team_id):
        abort(404)
    if req.status != "awaiting_original_match":
        flash("This offer is no longer awaiting a match/reject decision.", "warn")
        return redirect(url_for("site_gm.rfa_offers_page"))
    player = db.session.get(Player, int(req.player_id))
    offering_team = db.session.get(Team, int(req.offering_team_id))
    rights_team = db.session.get(Team, int(req.rights_team_id))
    if request.method == "POST":
        decision = (request.form.get("decision") or "").strip().lower()
        if decision not in ("match", "reject"):
            flash("Choose Match or Reject.", "err")
            return redirect(url_for("site_gm.rfa_offer_respond", rid=rid))
        req.original_team_decision = decision
        req.original_team_user_id = int(current_user.id)
        req.original_team_decided_at = datetime.utcnow()
        req.processed_at = datetime.utcnow()
        req.status = "original_matched" if decision == "match" else "original_rejected"
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action=f"rfa_original_{decision}",
                detail_json=json.dumps({"request_id": int(req.id), "player_id": int(req.player_id)}),
            )
        )
        commit_with_sqlite_retry(db.session)
        notify_rfa_original_team_decision(slug, req, player=player, offering_team=offering_team)
        flash(
            "You matched the offer — player stays with your club."
            if decision == "match"
            else "You declined to match — the offering team may proceed.",
            "ok",
        )
        return redirect(url_for("site_gm.rfa_offers_page"))
    return render_template(
        "rfa_offer_respond.html",
        req=req,
        player=player,
        offering_team=offering_team,
        rights_team=rights_team,
        category_labels=CATEGORY_LABELS,
        happiness_label=happiness_label,
    )


@site_gm_bp.get("/staff/<staff_fhm_id>")
@login_required
def staff_profile_page(staff_fhm_id: str):
    if not _membership() and not getattr(current_user, "is_admin", False):
        flash("Staff profiles are available to active GMs and league admins.", "err")
        return redirect(url_for("main.home"))
    slug = _league_slug()
    prof = get_staff_profile(staff_fhm_id)
    if prof is None:
        abort(404)
    img = staff_image_url(slug, prof.get("staff_fhm_id")) or staff_placeholder_url()
    view = build_staff_profile_view(prof)
    return render_template(
        "staff.html",
        staff=prof,
        staff_image_url=img,
        staff_placeholder_url=staff_placeholder_url(),
        staff_sections=view["sections"],
        staff_primary_overall=view["primary_overall"],
        staff_primary_role_label=view["primary_role_label"],
    )


@site_gm_bp.get("/operations/trade-proposal/<int:pid>")
@login_required
def trade_proposal_detail(pid: int):
    flash("Official trades are entered and published by the league office.", "err")
    return redirect(url_for("main.trade_log_page"))


@site_gm_bp.post("/operations/trade-proposal/<int:pid>/respond")
@login_required
def trade_proposal_partner_respond(pid: int):
    flash("Official trades are entered and published by the league office.", "err")
    return redirect(url_for("main.trade_log_page"))


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

    row = db.session.get(GmInAppNotification, nid)
    if not row or row.user_id != current_user.id or row.league_slug != slug:
        abort(404)
    kind = str(row.kind or "")
    article_id = int(row.article_id) if row.article_id else None

    if row.read_at is None:
        read_at = datetime.utcnow()
        notification_id = int(row.id)
        user_id = int(current_user.id)

        def _mark_notification_read() -> None:
            from sqlalchemy import update

            db.session.execute(
                update(GmInAppNotification)
                .where(
                    GmInAppNotification.id == notification_id,
                    GmInAppNotification.user_id == user_id,
                    GmInAppNotification.league_slug == slug,
                    GmInAppNotification.read_at.is_(None),
                )
                .values(read_at=read_at)
            )

        write_with_sqlite_retry(db.session, _mark_notification_read)
    if kind == "news_approved" and article_id:
        return redirect(url_for("main.league_headlines", article=article_id) + f"#a{article_id}")
    if kind == "admin_league_article" and article_id:
        return redirect(url_for("main.league_headlines", article=article_id) + f"#a{article_id}")
    if kind == "news_denied":
        return redirect(url_for("site_gm.league_news"))
    if kind == "redemption_approved":
        return redirect(url_for("site_gm.action_points_page"))
    if kind == "redemption_denied":
        return redirect(url_for("site_gm.action_points_page"))
    if kind == "admin_review_news" and article_id:
        return redirect(url_for("site_admin.admin_news_preview", aid=int(article_id)))
    if kind == "admin_review_ap" and article_id:
        return redirect(url_for("site_admin.ap_request_one", rid=int(article_id)))
    if kind == "admin_review_staff" and article_id:
        return redirect(url_for("site_admin.admin_staff_request_one", rid=int(article_id)))
    if kind == "admin_staff_payroll_adjust":
        return redirect(url_for("site_admin.admin_staff_budgets"))
    if kind in ("staff_hire_approved", "staff_fire_approved", "staff_change_denied"):
        return redirect(url_for("site_gm.staff_salaries_page"))
    if kind == "admin_review_rfa" and article_id:
        return redirect(url_for("site_admin.admin_rfa_offer_one", rid=int(article_id)))
    if kind in (
        "rfa_player_rejected",
        "rfa_awaiting_equalization",
        "rfa_original_matched",
        "rfa_original_rejected",
        "rfa_offer_completed",
    ):
        return redirect(url_for("site_gm.rfa_offers_page"))
    if kind == "rfa_awaiting_match" and article_id:
        return redirect(url_for("site_gm.rfa_offer_respond", rid=int(article_id)))
    if kind in ("trade_partner_review", "trade_outcome_proposer", "trade_outcome_partner") and article_id:
        return redirect(url_for("site_gm.trade_proposal_detail", pid=int(article_id)))
    if kind == "trade_commish_review" and article_id:
        return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=int(article_id)))
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
            send_gm_message(
                league_slug=slug,
                from_user_id=int(current_user.id),
                to_user_id=peer_user_id,
                body=body[:_GM_MESSAGE_MAX_LEN],
                event_key="gm_direct_message",
            )
            flash("Sent.", "ok")
        return redirect(url_for("site_gm.gm_messages_thread", peer_user_id=peer_user_id))

    mark_thread_read(slug, current_user.id, peer_user_id)
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
    summary = format_ledger_summary(
        db.session, from_team, to_team, left_out, right_out, league_slug=slug
    )
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "republish_news":
            if prop.status != STATUS_PUBLISHED:
                flash("Only published proposals can be repaired/requeued.", "err")
                return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=pid))
            from_article_id, _to_article_id = publish_trade_news_articles(
                db.session,
                league_slug=slug,
                proposal=prop,
                commissioner_user_id=int(prop.commissioner_user_id or current_user.id),
            )
            _enqueue_confirmed_trade_discord(
                proposal=prop,
                proposal_id=int(prop.id),
                article_id=from_article_id,
                from_team=from_team,
                to_team=to_team,
                team=from_team,
            )
            commit_with_sqlite_retry(db.session)
            flash("Trade news verified and Discord confirmation was queued if missing.", "ok")
            return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=pid))
        if prop.status != STATUS_PENDING_COMMISSIONER:
            flash("This proposal is not awaiting commissioner action.", "err")
            return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=pid))
        if action == "approve":
            from_article_id, moved_draft_picks, err = publish_trade_proposal(
                db.session,
                db.session,
                league_slug=slug,
                proposal=prop,
                commissioner_user_id=int(current_user.id),
                raw_dir=_trade_tool_raw_dir(),
                notify_gms=True,
            )
            if err:
                flash(
                    f"Ledger no longer validates against current rosters ({err}). "
                    "Update CSVs before approving.",
                    "err",
                )
                return redirect(url_for("site_admin.admin_trade_proposal_detail", pid=pid))
            _enqueue_confirmed_trade_discord(
                proposal=prop,
                proposal_id=int(prop.id),
                article_id=from_article_id,
                from_team=from_team,
                to_team=to_team,
                team=from_team,
            )
            commit_with_sqlite_retry(db.session)
            msg = "Trade approved and published on the site for both teams."
            if moved_draft_picks:
                msg += f" Draft ownership updated for {len(moved_draft_picks)} pick(s)."
            flash(msg, "ok")
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
            commit_with_sqlite_retry(db.session)
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


@site_admin_bp.route("/draft-pick-ownership", methods=["GET", "POST"])
@login_required
def admin_draft_pick_ownership():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    reset_calendar_seeded_panels_if_needed(
        db.session,
        db.session,
        league_slug=slug,
    )
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "add_year":
            year = int(request.form.get("draft_year") or 0)
            rounds = max(1, min(15, int(request.form.get("round_count") or 9)))
            if year <= 0:
                flash("Enter a valid draft year.", "err")
                return redirect(url_for("site_admin.admin_draft_pick_ownership"))
            panel = db.session.scalar(
                select(DraftPickOwnershipYear).where(
                    DraftPickOwnershipYear.league_slug == slug,
                    DraftPickOwnershipYear.draft_year == year,
                ).limit(1)
            )
            if panel is None:
                panel = DraftPickOwnershipYear(
                    league_slug=slug,
                    draft_year=year,
                    round_count=rounds,
                    status="active",
                    display_order=9999,
                )
                db.session.add(panel)
                commit_with_sqlite_retry(db.session)
            else:
                panel.round_count = rounds
                panel.status = "active"
                panel.manual_status_override = True
                commit_with_sqlite_retry(db.session)
            ensure_draft_pick_ownership_panels(
                db.session,
                db.session,
                league_slug=slug,
                active_count=3,
                default_round_count=rounds,
                exclude_years={int(year)},
            )
            flash(f"Draft panel for {year} is ready.", "ok")
            return redirect(url_for("site_admin.admin_draft_pick_ownership"))
        if action == "set_status":
            panel_id = int(request.form.get("panel_id") or 0)
            panel = db.session.get(DraftPickOwnershipYear, panel_id)
            if panel is None or panel.league_slug != slug:
                flash("Draft panel not found.", "err")
                return redirect(url_for("site_admin.admin_draft_pick_ownership"))
            status = (request.form.get("status") or "").strip().lower()
            if status not in {"active", "completed"}:
                flash("Choose a valid draft panel status.", "err")
                return redirect(url_for("site_admin.admin_draft_pick_ownership"))
            panel.status = status
            panel.manual_status_override = True
            commit_with_sqlite_retry(db.session)
            flash(f"{panel.draft_year} Draft Ownership is now {status.title()}.", "ok")
            return redirect(url_for("site_admin.admin_draft_pick_ownership"))
        if action == "reactivate_year":
            year = int(request.form.get("draft_year") or 0)
            panel = db.session.scalar(
                select(DraftPickOwnershipYear).where(
                    DraftPickOwnershipYear.league_slug == slug,
                    DraftPickOwnershipYear.draft_year == year,
                ).limit(1)
            )
            if panel is None:
                flash("Draft panel not found.", "err")
                return redirect(url_for("site_admin.admin_draft_pick_ownership"))
            panel.status = "active"
            panel.manual_status_override = True
            commit_with_sqlite_retry(db.session)
            ensure_draft_pick_ownership_panels(
                db.session,
                db.session,
                league_slug=slug,
                active_count=3,
                default_round_count=max(1, int(panel.round_count or 9)),
                exclude_years={int(year)},
            )
            flash(f"{year} Draft Ownership is now Active.", "ok")
            return redirect(url_for("site_admin.admin_draft_pick_ownership"))
        if action == "save_year":
            panel_id = int(request.form.get("panel_id") or 0)
            panel = db.session.get(DraftPickOwnershipYear, panel_id)
            if panel is None or panel.league_slug != slug:
                flash("Draft panel not found.", "err")
                return redirect(url_for("site_admin.admin_draft_pick_ownership"))
            rounds = max(1, min(15, int(request.form.get("round_count") or panel.round_count or 9)))
            owner_by_key: dict[tuple[int, int], int] = {}
            team_ids = {int(t.id) for t in draft_pick_teams_for_grid(db.session)}
            for key, value in request.form.items():
                if not key.startswith("owner_"):
                    continue
                parts = key.split("_")
                if len(parts) != 3:
                    continue
                try:
                    original_fhm = int(parts[1])
                    rnd = int(parts[2])
                    owner_team_id = int(value)
                except ValueError:
                    continue
                if owner_team_id not in team_ids:
                    continue
                owner_by_key[(original_fhm, rnd)] = owner_team_id
            changed = save_draft_pick_ownership_year_grid(
                db.session,
                db.session,
                league_slug=slug,
                draft_year=int(panel.draft_year),
                round_count=rounds,
                owner_by_key=owner_by_key,
            )
            flash(f"Saved {panel.draft_year} ownership grid ({changed} updated cells).", "ok")
            return redirect(url_for("site_admin.admin_draft_pick_ownership"))
        flash("Unknown action.", "err")
        return redirect(url_for("site_admin.admin_draft_pick_ownership"))
    panels = ensure_draft_pick_ownership_panels(
        db.session,
        db.session,
        league_slug=slug,
        active_count=3,
        default_round_count=9,
    )
    active_panels = [
        panel
        for panel in panels
        if str(panel.status or "active") != "completed"
    ]
    completed_years = sorted(
        {
            int(panel.draft_year)
            for panel in panels
            if str(panel.status or "active") == "completed"
        }
    )
    teams = draft_pick_teams_for_grid(db.session)
    team_choices = [
        {
            "id": int(t.id),
            "abbr": (t.abbreviation or "").strip() or f"T{t.id}",
            "name": t.full_display_name(),
            "team": t,
        }
        for t in teams
    ]
    logo_bundle = get_season_team_logo_bundle()

    def _admin_draft_pick_logo_url(team: Team, logo_year: int) -> str:
        return logo_bundle.team_logo_url_for_season_context(team, int(logo_year))

    panels_view: list[dict[str, object]] = []
    max_year = 0
    for panel in active_panels:
        max_year = max(max_year, int(panel.draft_year))
        logo_year = int(panel.draft_year)
        grid_rows = build_draft_pick_ownership_year_grid(
            db.session,
            db.session,
            league_slug=slug,
            draft_year=int(panel.draft_year),
            round_count=max(1, int(panel.round_count)),
        )
        team_logo_urls = {
            int(t.id): _admin_draft_pick_logo_url(t, logo_year)
            for t in teams
        }
        panels_view.append(
            {
                "panel": panel,
                "grid_rows": grid_rows,
                "rounds": list(range(1, max(1, int(panel.round_count)) + 1)),
                "team_logo_urls": team_logo_urls,
            }
        )
    if max_year <= 0 and completed_years:
        max_year = max(completed_years)
    next_year = (max_year + 1) if max_year > 0 else datetime.utcnow().year
    return render_template(
        "admin_draft_pick_ownership.html",
        league_slug=slug,
        panels_view=panels_view,
        completed_years=completed_years,
        team_choices=team_choices,
        next_year=next_year,
    )


@site_admin_bp.route("/game-records", methods=["GET", "POST"])
@login_required
def admin_game_records():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    from app.services.game_records import (
        MANUAL_BASELINE_NOTE,
        baseline_team_choices_for_admin,
        delete_baseline,
        list_baselines,
        metric_choices_for_admin,
        upsert_baseline,
    )

    players = db.session.scalars(select(Player).order_by(Player.last_name, Player.first_name)).all()
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    team_choices = baseline_team_choices_for_admin(db.session)
    metric_choices = metric_choices_for_admin()

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "delete":
            bid = request.form.get("baseline_id")
            try:
                baseline_id = int(bid or "")
            except (TypeError, ValueError):
                flash("Invalid baseline id.", "err")
                return redirect(url_for("site_admin.admin_game_records"))
            if delete_baseline(db.session, baseline_id):
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="game_records_baseline_delete",
                        detail_json=json.dumps({"baseline_id": baseline_id}),
                    )
                )
                commit_with_sqlite_retry(db.session)
                flash("Deleted game record baseline.", "ok")
            else:
                flash("Baseline not found.", "err")
            return redirect(url_for("site_admin.admin_game_records"))

        if action in ("save", "create"):
            metric_key = (request.form.get("metric_key") or "").strip()
            segment = (request.form.get("segment") or "rs").strip().lower()
            scope = (request.form.get("scope") or "all").strip().lower()
            player_kind = (request.form.get("player_kind") or "skater").strip().lower()
            raw_val = (request.form.get("value") or "").strip()
            try:
                value = float(raw_val)
            except ValueError:
                flash("Record value must be a number.", "err")
                return redirect(url_for("site_admin.admin_game_records"))
            if segment not in ("rs", "po") or scope not in ("all", "rookie"):
                flash("Invalid segment or scope.", "err")
                return redirect(url_for("site_admin.admin_game_records"))
            valid_keys = {k for k, _, kind in metric_choices if kind == player_kind}
            if metric_key not in valid_keys:
                flash("Invalid metric.", "err")
                return redirect(url_for("site_admin.admin_game_records"))

            def _opt_int(name: str) -> int | None:
                raw = (request.form.get(name) or "").strip()
                if not raw:
                    return None
                try:
                    return int(raw)
                except ValueError:
                    return None

            game_date_raw = (request.form.get("game_date") or "").strip()
            game_date_val = None
            if game_date_raw:
                try:
                    game_date_val = date.fromisoformat(game_date_raw)
                except ValueError:
                    flash("Invalid game date.", "err")
                    return redirect(url_for("site_admin.admin_game_records"))

            notes_raw = (request.form.get("notes") or "").strip()
            row = upsert_baseline(
                db.session,
                metric_key=metric_key,
                segment=segment,
                scope=scope,
                player_kind=player_kind,
                value=value,
                player_id=_opt_int("player_id"),
                team_id=_opt_int("team_id"),
                opponent_team_id=_opt_int("opponent_team_id"),
                game_id=_opt_int("game_id"),
                game_date=game_date_val,
                season_label=(request.form.get("season_label") or "").strip() or None,
                notes=notes_raw or MANUAL_BASELINE_NOTE,
            )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="game_records_baseline_save",
                    detail_json=json.dumps(
                        {
                            "baseline_id": int(row.id or 0),
                            "metric_key": metric_key,
                            "segment": segment,
                            "scope": scope,
                            "player_kind": player_kind,
                            "value": value,
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash("Saved game record baseline.", "ok")
            return redirect(url_for("site_admin.admin_game_records"))

        return redirect(url_for("site_admin.admin_game_records"))

    baselines = list_baselines(db.session)
    return render_template(
        "admin_game_records.html",
        league_slug=slug,
        baselines=baselines,
        players=players,
        teams=teams,
        team_choices=team_choices,
        metric_choices=metric_choices,
    )


def _admin_history_player_team_choices():
    players = db.session.scalars(select(Player).order_by(Player.last_name, Player.first_name)).all()
    teams = db.session.scalars(select(Team).order_by(Team.name)).all()
    return players, teams


@site_admin_bp.route("/records")
@login_required
def admin_records_home():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    return render_template("admin_records_home.html", league_slug=_league_slug())


@site_admin_bp.route("/history-records")
@login_required
def admin_history_records_home():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    return render_template("admin_records_home.html", league_slug=_league_slug())


@site_admin_bp.route("/hall-of-fame", methods=["GET", "POST"])
@login_required
def admin_hall_of_fame():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    from app.services.admin_hall_of_fame import (
        delete_hof_member,
        list_hof_admin,
        upsert_hof_member,
    )

    def _render(*, edit_row=None, form_values=None):
        return render_template(
            "admin_hall_of_fame.html",
            rows=list_hof_admin(db.session),
            edit_row=edit_row,
            form_values=form_values or {},
        )

    edit_id = request.args.get("edit", type=int)
    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "delete":
            mid = int(request.form.get("member_id") or 0)
            if delete_hof_member(db.session, mid):
                _audit("admin_hall_of_fame_delete", {"member_id": mid})
                commit_with_sqlite_retry(db.session)
                flash("Hall of Fame inductee removed.", "ok")
            else:
                db.session.rollback()
                flash("Hall of Fame row not found.", "err")
            return redirect(url_for("site_admin.admin_hall_of_fame"))

        member_id = int(request.form.get("member_id") or 0) or None
        player_name = (request.form.get("player_name") or "").strip()
        selected_player_id = int(request.form.get("player_id") or 0) or None
        member_kind = (request.form.get("member_kind") or "").strip()
        inducted_year_raw = (request.form.get("inducted_year") or "").strip()
        try:
            inducted_year = int(inducted_year_raw or "0")
        except ValueError:
            inducted_year = 0
        form_values = {
            "player_name": player_name,
            "player_id": selected_player_id or "",
            "member_kind": member_kind or "skater",
            "inducted_year": inducted_year_raw,
        }
        row, err = upsert_hof_member(
            db.session,
            member_id=member_id,
            player_name=player_name,
            player_id=selected_player_id,
            member_kind=member_kind,
            inducted_year=inducted_year,
            user_id=int(current_user.id),
        )
        if err:
            db.session.rollback()
            flash(err, "err")
            edit_row = db.session.get(HallOfFameMember, member_id) if member_id else None
            return _render(edit_row=edit_row, form_values=form_values)

        assert row is not None
        _audit(
            "admin_hall_of_fame_save",
            {
                "member_id": int(row.id),
                "player_id": int(row.player_id),
                "member_kind": row.member_kind,
                "inducted_year": int(row.inducted_year),
            },
        )
        try:
            commit_with_sqlite_retry(db.session)
        except Exception:
            db.session.rollback()
            flash("Could not save Hall of Fame inductee. Please try again.", "err")
            edit_row = db.session.get(HallOfFameMember, member_id) if member_id else None
            return _render(edit_row=edit_row, form_values=form_values)
        flash("Hall of Fame inductee saved.", "ok")
        return redirect(url_for("site_admin.admin_hall_of_fame"))

    edit_row = db.session.get(HallOfFameMember, edit_id) if edit_id else None
    return _render(edit_row=edit_row)


@site_admin_bp.route("/records/career-adjustments", methods=["GET", "POST"])
@login_required
def admin_record_career_adjustments():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    from app.models import RecordStatAdjustment
    from app.services.record_stat_adjustments import (
        clear_adjustment_cache,
        delete_adjustment,
        list_adjustments,
        parse_career_adjustment_form,
        upsert_career_adjustment,
    )

    slug = _league_slug()
    players, _teams = _admin_history_player_team_choices()
    edit_id = request.args.get("edit", type=int)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "delete":
            aid = request.form.get("adjustment_id", type=int)
            if aid and delete_adjustment(db.session, aid):
                clear_adjustment_cache()
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="record_career_adjustment_delete",
                        detail_json=json.dumps({"adjustment_id": aid}),
                    )
                )
                commit_with_sqlite_retry(db.session)
                flash("Deleted career-line adjustment.", "ok")
            else:
                flash("Adjustment not found.", "err")
            return redirect(url_for("site_admin.admin_record_career_adjustments"))

        if action in ("save", "create"):
            fields = parse_career_adjustment_form(request.form)
            aid = request.form.get("adjustment_id", type=int)
            try:
                if fields["player_id"] is None or fields["season_year"] is None:
                    raise ValueError("Player and season year are required.")
                row = upsert_career_adjustment(
                    db.session,
                    adjustment_id=aid,
                    adj_type=str(fields["adj_type"]),
                    line_kind=str(fields["line_kind"]),
                    player_id=int(fields["player_id"]),
                    season_year=int(fields["season_year"]),
                    team_fhm_id=fields["team_fhm_id"],
                    career_source=fields["career_source"],
                    overrides=fields["overrides"],
                    notes=fields["notes"],
                    user_id=int(current_user.id),
                )
            except ValueError as exc:
                flash(str(exc), "err")
                return redirect(url_for("site_admin.admin_record_career_adjustments"))
            clear_adjustment_cache()
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="record_career_adjustment_save",
                    detail_json=json.dumps(
                        {
                            "adjustment_id": int(row.id or 0),
                            "player_id": row.player_id,
                            "season_year": row.season_year,
                            "adj_type": row.adj_type,
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash("Saved career-line adjustment. Records pages will recalculate on refresh.", "ok")
            return redirect(url_for("site_admin.admin_record_career_adjustments"))

    rows = list_adjustments(db.session)
    edit_row = db.session.get(RecordStatAdjustment, edit_id) if edit_id else None
    return render_template(
        "admin_record_career_adjustments.html",
        rows=rows,
        players=players,
        edit_row=edit_row,
    )


@site_admin_bp.route("/history-records/team-seasons", methods=["GET", "POST"])
@login_required
def admin_history_team_seasons():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    from app.services.admin_history_records import (
        delete_team_season_record,
        list_team_season_records,
        parse_team_season_form,
        upsert_team_season_record,
    )

    slug = _league_slug()
    players, teams = _admin_history_player_team_choices()
    season_filter = (request.args.get("season") or request.form.get("season_filter") or "").strip()
    edit_id = request.args.get("edit", type=int)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "delete":
            rid = request.form.get("record_id", type=int)
            if rid and delete_team_season_record(db.session, rid):
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="history_team_season_delete",
                        detail_json=json.dumps({"record_id": rid}),
                    )
                )
                commit_with_sqlite_retry(db.session)
                flash("Deleted team season record.", "ok")
            else:
                flash("Record not found.", "err")
            return redirect(
                url_for("site_admin.admin_history_team_seasons", season=season_filter or None)
            )

        if action in ("save", "create"):
            fields = parse_team_season_form(request.form)
            rid = request.form.get("record_id", type=int)
            try:
                row = upsert_team_season_record(
                    db.session,
                    record_id=rid,
                    user_id=int(current_user.id),
                    **fields,
                )
            except ValueError as exc:
                flash(str(exc), "err")
                return redirect(
                    url_for("site_admin.admin_history_team_seasons", season=season_filter or None)
                )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="history_team_season_save",
                    detail_json=json.dumps(
                        {
                            "record_id": int(row.id or 0),
                            "season_year_label": row.season_year_label,
                            "team_id": row.team_id,
                        }
                    ),
                )
            )
            try:
                commit_with_sqlite_retry(db.session)
            except Exception:
                db.session.rollback()
                flash("Could not save — duplicate season/team row may already exist.", "err")
                return redirect(
                    url_for("site_admin.admin_history_team_seasons", season=season_filter or None)
                )
            flash("Saved team season record.", "ok")
            return redirect(
                url_for(
                    "site_admin.admin_history_team_seasons",
                    season=fields.get("season_year_label") or season_filter or None,
                )
            )

    rows = list_team_season_records(db.session, season_filter=season_filter or None)
    edit_row = db.session.get(TeamSeasonRecord, edit_id) if edit_id else None
    return render_template(
        "admin_history_team_seasons.html",
        league_slug=slug,
        rows=rows,
        teams=teams,
        players=players,
        season_filter=season_filter,
        edit_row=edit_row,
    )


@site_admin_bp.route("/history-records/awards", methods=["GET", "POST"])
@login_required
def admin_history_awards():
    """Legacy URL — Season Awards admin consolidated at ``admin_awards``."""
    season = (request.args.get("season") or request.form.get("season_filter") or "").strip()
    edit = request.args.get("edit", type=int)
    return redirect(url_for("site_admin.admin_awards_tracker", season=season or None, edit_award=edit or None))


@site_admin_bp.route("/history-records/all-stars", methods=["GET", "POST"])
@login_required
def admin_history_all_stars():
    """Legacy URL — Season Awards admin consolidated at ``admin_awards``."""
    season = (request.args.get("season") or request.form.get("season_filter") or "").strip()
    return redirect(url_for("site_admin.admin_awards_tracker", season=season or None))


@site_admin_bp.route("/rule-strikes", methods=["GET", "POST"])
@login_required
def admin_rule_strikes():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if slug != "bowl-cap":
        abort(404)
    cycle_year = active_cycle_year(db.session, league_slug=slug)
    rows, _active = strike_grid_rows(
        db.session,
        db.session,
        league_slug=slug,
        cycle_year=cycle_year,
    )
    if request.method == "POST":
        selected: dict[int, set[int]] = {}
        for row in rows:
            tid = int(row["team_id"])
            strikes: set[int] = set()
            for strike_no in (1, 2, 3):
                if request.form.get(f"strike_{tid}_{strike_no}") == "1":
                    strikes.add(strike_no)
            if strikes:
                selected[tid] = strikes
        created, teams_with_any = save_cycle_strikes(
            db.session,
            league_slug=slug,
            cycle_year=cycle_year,
            selected=selected,
            admin_user_id=int(current_user.id),
        )
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="cap_rule_strikes_save",
                detail_json=json.dumps(
                    {
                        "cycle_year": int(cycle_year),
                        "strike_rows": int(created),
                        "teams_with_any_strike": int(teams_with_any),
                    }
                ),
            )
        )
        commit_with_sqlite_retry(db.session)
        flash(
            f"Saved strike tracker for cycle {cycle_year} ({created} active strike row(s)).",
            "ok",
        )
        return redirect(url_for("site_admin.admin_rule_strikes"))
    strike_round_map = {int(k): int(v) for k, v in STRIKE_TO_ROUND.items()}
    return render_template(
        "admin_rule_strikes.html",
        league_slug=slug,
        cycle_year=int(cycle_year),
        rows=rows,
        strike_round_map=strike_round_map,
    )


@site_admin_bp.get("/commissioner-sop")
@login_required
def admin_commissioner_sop():
    require_admin()
    return render_template("admin_commissioner_sop.html")


@site_admin_bp.route("/franchise-identities", methods=["GET", "POST"])
@login_required
def admin_franchise_identities():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "seed_csv":
            from app.services.franchise_identities import seed_franchise_identities_from_csv

            raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
            result = seed_franchise_identities_from_csv(db.session, raw_dir / "team_identity_history.csv")
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="franchise_identity_seed_csv",
                    detail_json=json.dumps(
                        {
                            "created": result.created,
                            "updated": result.updated,
                            "skipped": result.skipped,
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash(
                f"Seeded franchise identities: {result.created} created, "
                f"{result.updated} updated, {result.skipped} skipped.",
                "ok",
            )
            return redirect(url_for("site_admin.admin_franchise_identities"))
        if action == "delete":
            rid = request.form.get("identity_id", type=int)
            row = db.session.get(FranchiseTeamIdentity, rid) if rid else None
            if row:
                db.session.delete(row)
                commit_with_sqlite_retry(db.session)
                flash("Franchise identity deleted.", "ok")
            return redirect(url_for("site_admin.admin_franchise_identities"))

        rid = request.form.get("identity_id", type=int)
        row = db.session.get(FranchiseTeamIdentity, rid) if rid else None
        if row is None:
            row = FranchiseTeamIdentity()
        team_id = request.form.get("team_id", type=int)
        team = db.session.get(Team, team_id) if team_id else None
        row.team_id = int(team.id) if team else None
        row.team_fhm_id = (request.form.get("team_fhm_id") or (team.fhm_team_id if team else "") or "").strip() or None
        row.display_name = (request.form.get("display_name") or "").strip()
        row.abbreviation = (request.form.get("abbreviation") or "").strip() or None
        row.logo_file = (request.form.get("logo_file") or "").strip() or None
        row.status = (request.form.get("status") or "historical").strip() or "historical"
        row.notes = (request.form.get("notes") or "").strip()
        try:
            row.start_year = int((request.form.get("start_year") or "").strip())
        except (TypeError, ValueError):
            flash("Start year is required.", "err")
            return redirect(url_for("site_admin.admin_franchise_identities"))
        end_raw = (request.form.get("end_year") or "").strip()
        if end_raw:
            try:
                row.end_year = int(end_raw)
            except ValueError:
                flash("End year must be blank or a year.", "err")
                return redirect(url_for("site_admin.admin_franchise_identities"))
        else:
            row.end_year = None
        if not row.display_name:
            flash("Display name is required.", "err")
            return redirect(url_for("site_admin.admin_franchise_identities"))
        if row.team_id is None and not row.team_fhm_id:
            flash("Pick a current franchise or provide an FHM team id.", "err")
            return redirect(url_for("site_admin.admin_franchise_identities"))
        db.session.add(row)
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="franchise_identity_save",
                detail_json=json.dumps(
                    {
                        "identity_id": getattr(row, "id", None),
                        "team_id": row.team_id,
                        "team_fhm_id": row.team_fhm_id,
                        "display_name": row.display_name,
                        "start_year": row.start_year,
                        "end_year": row.end_year,
                    }
                ),
            )
        )
        commit_with_sqlite_retry(db.session)
        flash("Franchise identity saved.", "ok")
        return redirect(url_for("site_admin.admin_franchise_identities"))

    rows = db.session.scalars(
        select(FranchiseTeamIdentity)
        .options(joinedload(FranchiseTeamIdentity.team))
        .order_by(
            FranchiseTeamIdentity.start_year.desc(),
            FranchiseTeamIdentity.display_name.asc(),
            FranchiseTeamIdentity.id.desc(),
        )
    ).all()
    edit_id = request.args.get("edit", type=int)
    edit_row = db.session.get(FranchiseTeamIdentity, edit_id) if edit_id else None
    return render_template(
        "admin_franchise_identities.html",
        rows=rows,
        teams=_franchise_identity_team_options(),
        edit_row=edit_row,
    )


@site_admin_bp.route("/team-honors", methods=["GET", "POST"])
@login_required
def admin_team_honors():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    from app.services.team_honors import ensure_team_honors_meta
    from app.services.team_honors_media import (
        save_retired_jersey_image,
        save_victory_banner_image,
    )

    slug = _league_slug()
    teams = _franchise_identity_team_options()
    team_id = request.args.get("team_id", type=int) or request.form.get("team_id", type=int)
    team = db.session.get(Team, team_id) if team_id else None

    def _clean_hex_color(raw: str | None, fallback: str = "#111827") -> str:
        value = str(raw or "").strip()
        if not value.startswith("#"):
            value = f"#{value}"
        if len(value) == 7 and all(ch in "0123456789abcdefABCDEF" for ch in value[1:]):
            return value.lower()
        return fallback

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if not team:
            flash("Select a team first.", "err")
            return redirect(url_for("site_admin.admin_team_honors"))

        if action == "save_meta":
            meta = ensure_team_honors_meta(db.session, int(team.id))
            meta.retired_section_enabled = request.form.get("retired_section_enabled") == "on"
            db.session.add(meta)
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="team_honors_meta_save",
                    detail_json=json.dumps(
                        {
                            "team_id": int(team.id),
                            "retired_section_enabled": meta.retired_section_enabled,
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash("Team honors settings saved.", "ok")
            return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))

        if action == "delete_retired":
            rid = request.form.get("retired_id", type=int)
            row = db.session.get(TeamRetiredNumber, rid) if rid else None
            if row and int(row.team_id) == int(team.id):
                db.session.delete(row)
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="team_retired_number_delete",
                        detail_json=json.dumps({"team_id": int(team.id), "retired_id": rid}),
                    )
                )
                commit_with_sqlite_retry(db.session)
                flash("Retired number removed.", "ok")
            return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))

        if action == "delete_banner":
            bid = request.form.get("banner_id", type=int)
            row = db.session.get(TeamVictoryBanner, bid) if bid else None
            if row and int(row.team_id) == int(team.id):
                db.session.delete(row)
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="team_victory_banner_delete",
                        detail_json=json.dumps({"team_id": int(team.id), "banner_id": bid}),
                    )
                )
                commit_with_sqlite_retry(db.session)
                flash("Victory banner removed.", "ok")
            return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))

        if action == "save_retired":
            rid = request.form.get("retired_id", type=int)
            row = db.session.get(TeamRetiredNumber, rid) if rid else None
            if row is None:
                row = TeamRetiredNumber(team_id=int(team.id))
            player_name = (request.form.get("player_name") or "").strip()
            if not player_name:
                flash("Player name is required.", "err")
                return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))
            try:
                jersey_number = int((request.form.get("jersey_number") or "").strip())
            except (TypeError, ValueError):
                flash("Jersey number is required.", "err")
                return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))
            row.player_name = player_name
            row.jersey_number = jersey_number
            default_number_color = team.text_color or team.primary_color or "#111827"
            row.number_color = _clean_hex_color(
                request.form.get("number_color"),
                _clean_hex_color(default_number_color),
            )
            row.is_active = request.form.get("is_active") == "on"
            try:
                row.sort_order = int((request.form.get("sort_order") or "0").strip())
            except ValueError:
                row.sort_order = 0
            row.notes = (request.form.get("notes") or "").strip()
            rel = save_retired_jersey_image(
                request.files.get("jersey_image"),
                league_slug=slug,
                team_id=int(team.id),
                jersey_number=jersey_number,
            )
            if rel:
                row.jersey_image_rel_path = rel
            elif not row.jersey_image_rel_path and not rid:
                flash("Upload a jersey image for new retired numbers.", "err")
                return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))
            db.session.add(row)
            db.session.flush()
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="team_retired_number_save",
                    detail_json=json.dumps(
                        {
                            "team_id": int(team.id),
                            "retired_id": row.id,
                            "jersey_number": row.jersey_number,
                            "player_name": row.player_name,
                        }
                    ),
                )
            )
            try:
                commit_with_sqlite_retry(db.session)
            except Exception:
                db.session.rollback()
                flash("That jersey number is already retired for this team.", "err")
                return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))
            flash("Retired number saved.", "ok")
            return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))

        if action == "save_banner":
            bid = request.form.get("banner_id", type=int)
            row = db.session.get(TeamVictoryBanner, bid) if bid else None
            if row is None:
                row = TeamVictoryBanner(team_id=int(team.id))
            try:
                victory_number = int((request.form.get("victory_number") or "").strip())
            except (TypeError, ValueError):
                flash("Victory number is required (used for sorting).", "err")
                return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))
            row.title = (request.form.get("title") or "").strip()
            row.victory_number = victory_number
            row.is_active = request.form.get("is_active") == "on"
            try:
                row.sort_order = int((request.form.get("sort_order") or "0").strip())
            except ValueError:
                row.sort_order = 0
            row.notes = (request.form.get("notes") or "").strip()
            rel = save_victory_banner_image(
                request.files.get("banner_image"),
                league_slug=slug,
                team_id=int(team.id),
                victory_number=victory_number,
            )
            if rel:
                row.banner_image_rel_path = rel
            elif not row.banner_image_rel_path and not bid:
                flash("Upload a banner image for new victory banners.", "err")
                return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))
            db.session.add(row)
            db.session.flush()
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="team_victory_banner_save",
                    detail_json=json.dumps(
                        {
                            "team_id": int(team.id),
                            "banner_id": row.id,
                            "victory_number": row.victory_number,
                        }
                    ),
                )
            )
            try:
                commit_with_sqlite_retry(db.session)
            except Exception:
                db.session.rollback()
                flash("That victory number already exists for this team.", "err")
                return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))
            flash("Victory banner saved.", "ok")
            return redirect(url_for("site_admin.admin_team_honors", team_id=team.id))

    honors_meta = None
    retired_rows: list[TeamRetiredNumber] = []
    banner_rows: list[TeamVictoryBanner] = []
    edit_retired_id = request.args.get("edit_retired", type=int)
    edit_banner_id = request.args.get("edit_banner", type=int)
    edit_retired = None
    edit_banner = None
    if team:
        honors_meta = ensure_team_honors_meta(db.session, int(team.id))
        commit_with_sqlite_retry(db.session)
        retired_rows = list(
            db.session.scalars(
                select(TeamRetiredNumber)
                .where(TeamRetiredNumber.team_id == team.id)
                .order_by(
                    TeamRetiredNumber.sort_order.asc(),
                    TeamRetiredNumber.jersey_number.asc(),
                    TeamRetiredNumber.id.asc(),
                )
            ).all()
        )
        banner_rows = list(
            db.session.scalars(
                select(TeamVictoryBanner)
                .where(TeamVictoryBanner.team_id == team.id)
                .order_by(
                    TeamVictoryBanner.sort_order.asc(),
                    TeamVictoryBanner.victory_number.asc(),
                    TeamVictoryBanner.id.asc(),
                )
            ).all()
        )
        if edit_retired_id:
            edit_retired = db.session.get(TeamRetiredNumber, edit_retired_id)
            if edit_retired and int(edit_retired.team_id) != int(team.id):
                edit_retired = None
        if edit_banner_id:
            edit_banner = db.session.get(TeamVictoryBanner, edit_banner_id)
            if edit_banner and int(edit_banner.team_id) != int(team.id):
                edit_banner = None

    return render_template(
        "admin_team_honors.html",
        teams=teams,
        team=team,
        honors_meta=honors_meta,
        retired_rows=retired_rows,
        banner_rows=banner_rows,
        edit_retired=edit_retired,
        edit_banner=edit_banner,
    )


@site_admin_bp.route("/join-league", methods=["GET", "POST"])
@login_required
def admin_join_league():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if request.method == "POST":
        action = (request.form.get("action") or "save").strip()
        if action == "test_smtp_login":
            from app.mail_util import test_smtp_login

            result = test_smtp_login()
            if result.get("ok"):
                flash(
                    f"SMTP login OK for {result.get('username')} "
                    f"(app password length {result.get('password_length')}).",
                    "ok",
                )
            else:
                hint = str(result.get("hint") or "").strip()
                flash(
                    f"SMTP login failed: {result.get('error')}"
                    + (f" {hint}" if hint else ""),
                    "err",
                )
            return redirect(url_for("site_admin.admin_join_league"))

        if action == "test_email":
            from app.mail_util import test_smtp_login

            login = test_smtp_login()
            if not login.get("ok"):
                flash(f"SMTP login failed (email not sent): {login.get('error')}", "err")
                return redirect(url_for("site_admin.admin_join_league"))

            recipient = (
                request.form.get("test_recipient")
                or getattr(current_user, "email", "")
                or current_app.config.get("JOIN_LEAGUE_RECIPIENT", "")
            )
            recipient = str(recipient or "").strip()
            try:
                send_site_email(
                    subject=f"[{current_app.config.get('LEAGUE_DISPLAY_NAME', 'League')}] Join League email test",
                    body=(
                        "This is a test of the Join Our League email settings.\n\n"
                        f"League: {current_app.config.get('LEAGUE_DISPLAY_NAME', '')}\n"
                        f"Public Join Our League page: {url_for('main.join_league', _external=True)}\n"
                        f"Admin availability page: {url_for('site_admin.admin_join_league', _external=True)}\n\n"
                        "If this message arrived and both links open, SMTP delivery and the join links are functional."
                    ),
                    to_addrs=[recipient],
                )
            except Exception as exc:
                flash(f"Could not send test email: {exc}", "err")
            else:
                flash(f"Test email sent to {recipient}.", "ok")
            return redirect(url_for("site_admin.admin_join_league"))

        selected = request.form.getlist("open_team")
        _, stale_options = _join_league_availability_rows()
        keep_stale = request.form.getlist("keep_stale_option")
        save_join_team_options([*selected, *[x for x in stale_options if x in keep_stale]])
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="join_league_availability_update",
                detail_json=json.dumps({"open_teams": selected, "kept_custom_options": keep_stale}),
            )
        )
        commit_with_sqlite_retry(db.session)
        flash("Join League availability updated.", "ok")
        return redirect(url_for("site_admin.admin_join_league"))

    from app.mail_util import test_smtp_login

    rows, stale_options = _join_league_availability_rows()
    configured_names, has_admin_file = configured_join_team_options()
    return render_template(
        "admin_join_league.html",
        rows=rows,
        stale_options=stale_options,
        configured_names=configured_names,
        has_admin_file=has_admin_file,
        teams_file=join_available_teams_path(),
        mail_settings=mail_settings_summary(),
        smtp_login=test_smtp_login(),
    )


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
        commit_with_sqlite_retry(db.session)
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
        commit_with_sqlite_retry(db.session)
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
    from app.services.bowl_six import (
        auto_update_bowl_six_slates,
        bowl_six_enabled,
        get_or_create_current_slate,
        list_slates,
        lock_at_display_eastern,
        lock_at_eastern_form_values,
        rs_games_in_slate_week,
        slate_gm_submission_roster_enriched,
        slate_week_rs_games_complete,
    )

    try:
        auto_update_bowl_six_slates(db.session, db.session, slug)
        commit_with_sqlite_retry(db.session)
    except Exception:
        db.session.rollback()
    bowl_six_on = bowl_six_enabled(db.session, slug)
    bowl_six_current = get_or_create_current_slate(db.session, slug) if bowl_six_on else None
    bowl_six_slates = list_slates(db.session, slug, limit=12) if bowl_six_on else []
    bowl_six_week_games_final = 0
    bowl_six_week_games_total = 0
    bowl_six_week_complete = False
    if bowl_six_on and bowl_six_current:
        week_games = rs_games_in_slate_week(db.session, bowl_six_current)
        bowl_six_week_games_total = len(week_games)
        bowl_six_week_games_final = sum(
            1 for g in week_games if (g.status or "").lower() == "final"
        )
        bowl_six_week_complete = slate_week_rs_games_complete(db.session, bowl_six_current)
        from app.services.bowl_six import sync_slate_lock_status

        sync_slate_lock_status(db.session, bowl_six_current)
        try:
            commit_with_sqlite_retry(db.session)
        except Exception:
            db.session.rollback()
        db.session.refresh(bowl_six_current)
    bowl_six_lock_et = (
        lock_at_eastern_form_values(bowl_six_current.lock_at)
        if bowl_six_current
        else {"lock_date": "", "lock_time": ""}
    )
    bowl_six_lock_display = (
        lock_at_display_eastern(bowl_six_current.lock_at) if bowl_six_current else ""
    )
    bowl_six_submissions = None
    if bowl_six_on and bowl_six_current and bowl_six_current.status != "skipped":
        bowl_six_submissions = slate_gm_submission_roster_enriched(
            db.session, db.session, slug, bowl_six_current
        )
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
        bowl_six_enabled=bowl_six_on,
        bowl_six_current=bowl_six_current,
        bowl_six_slates=bowl_six_slates,
        bowl_six_week_games_final=bowl_six_week_games_final,
        bowl_six_week_games_total=bowl_six_week_games_total,
        bowl_six_week_complete=bowl_six_week_complete,
        bowl_six_lock_et=bowl_six_lock_et,
        bowl_six_lock_display=bowl_six_lock_display,
        bowl_six_submissions=bowl_six_submissions,
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)

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
    commit_with_sqlite_retry(db.session)
    try:
        sync_salary_cap_schedule_rollover(db.session, db.session, league_slug=slug)
    except Exception as exc:
        current_app.logger.warning("Salary cap schedule rollover after season change: %s", exc)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
        trade_team = db.session.get(Team, int(row.team_id)) if row.team_id else None
        _enqueue_discord_event(
            "trade_request",
            trade_request_discord_payload(
                row,
                team_fields=team_fields_for_discord(trade_team),
            ),
            source_type="trade_request",
            source_id=int(row.id),
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
    commit_with_sqlite_retry(db.session)
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
        commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
        story_art = db.session.get(NewsArticle, int(row.article_id))
        if story_art and story_art.league_slug == slug:
            story_team = resolve_news_article_team(db.session, story_art)
            story_payload = news_article_discord_payload(
                story_art,
                schedule_id=int(row.id),
                channel=str(row.channel or ""),
                url=build_news_article_public_url(slug, story_art.id),
                **team_fields_for_discord(story_team),
            )
        else:
            story_payload = {
                "schedule_id": int(row.id),
                "article_id": int(row.article_id),
                "title": "Story published",
                "body": str(result.get("message") or "Story dispatched"),
                "body_preview": str(result.get("message") or "Story dispatched")[:280],
                "has_image": False,
                "channel": str(row.channel or ""),
                "url": build_news_article_public_url(slug, int(row.article_id)),
            }
        _enqueue_discord_event(
            "story_published",
            story_payload,
            source_type="story_schedule",
            source_id=int(row.id),
        )
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    """Assign season trophy winners and First/Second Team All-Stars (replaces voting-tracker scaffold)."""
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    from app.services.admin_history_records import (
        ALL_STAR_SLOT_DEFAULTS,
        all_stars_by_slot_for_season,
        apply_gm_winner_to_award_notes,
        award_name_choices_from_names,
        delete_history_award,
        gm_history_username,
        gm_user_id_from_award_notes,
        list_history_awards_admin,
        save_all_star_batch,
        season_label_choices_for_admin,
        sheet_season_from_notes,
        staff_award_winner_admin_label,
        upsert_history_award,
    )

    slug = _league_slug()
    players, teams = _admin_history_player_team_choices()
    season_filter = (request.args.get("season") or request.form.get("season_filter") or "").strip()
    edit_award_id = request.args.get("edit_award", type=int)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        if action == "delete_award":
            aid = request.form.get("award_id", type=int)
            if aid and delete_history_award(db.session, aid):
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="history_award_delete",
                        detail_json=json.dumps({"award_id": aid}),
                    )
                )
                commit_with_sqlite_retry(db.session)
                flash("Deleted award row.", "ok")
            else:
                flash("Award not found.", "err")
            return redirect(url_for("site_admin.admin_awards_tracker", season=season_filter or None))

        if action == "save_award":
            aid = request.form.get("award_id", type=int)
            season_label = season_filter or (request.form.get("season_label") or "").strip()
            award_name = (request.form.get("award_name") or "").strip()
            team_id = request.form.get("team_id", type=int)
            staff_fhm_id = (request.form.get("staff_fhm_id") or "").strip() or None
            notes = (request.form.get("notes") or "").strip() or None
            gm_user_id = request.form.get("gm_user_id", type=int)
            if gm_user_id:
                gm_user = db.session.get(User, gm_user_id)
                if gm_user is None:
                    flash("Selected GM not found.", "err")
                    return redirect(url_for("site_admin.admin_awards_tracker", season=season_label or None))
                mem = db.session.scalar(
                    select(GmLeagueMembership).where(
                        GmLeagueMembership.league_slug == slug,
                        GmLeagueMembership.user_id == int(gm_user_id),
                        GmLeagueMembership.status == "active",
                    ).limit(1)
                )
                if mem is None:
                    flash("Selected user is not an active GM in this league.", "err")
                    return redirect(url_for("site_admin.admin_awards_tracker", season=season_label or None))
                if not team_id:
                    team_id = int(mem.team_id)
                notes = apply_gm_winner_to_award_notes(
                    award_name,
                    notes,
                    gm_username=gm_history_username(gm_user),
                    gm_display=gm_display_name(gm_user),
                    gm_user_id=int(gm_user_id),
                )
                staff_fhm_id = None
            try:
                row = upsert_history_award(
                    db.session,
                    award_id=aid,
                    season_label=season_label,
                    league_slug=slug,
                    award_name=award_name,
                    player_id=request.form.get("player_id", type=int),
                    team_id=team_id,
                    staff_fhm_id=staff_fhm_id,
                    notes=notes,
                    user_id=int(current_user.id),
                )
            except ValueError as exc:
                flash(str(exc), "err")
                return redirect(url_for("site_admin.admin_awards_tracker", season=season_label or None))
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="history_award_save",
                    detail_json=json.dumps(
                        {
                            "award_id": int(row.id or 0),
                            "award_name": row.award_name,
                            "season_label": season_label,
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash("Saved award winner.", "ok")
            return redirect(url_for("site_admin.admin_awards_tracker", season=season_label or None))

        if action == "save_all_stars":
            season_label = (request.form.get("season_label") or season_filter or "").strip()
            if not season_label:
                flash("Season label is required.", "err")
                return redirect(url_for("site_admin.admin_awards_tracker"))
            total_saved = 0
            errors: list[str] = []
            for team_rank in (1, 2):
                saved, errs = save_all_star_batch(
                    db.session,
                    request.form,
                    season_label=season_label,
                    league_slug=slug,
                    team_rank=team_rank,
                    user_id=int(current_user.id),
                )
                total_saved += saved
                errors.extend(errs)
            if errors and not total_saved:
                flash(errors[0], "err")
            elif total_saved:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="history_all_star_save",
                        detail_json=json.dumps(
                            {"season_label": season_label, "slots_saved": total_saved}
                        ),
                    )
                )
                commit_with_sqlite_retry(db.session)
                flash(f"Saved {total_saved} all-star slot(s).", "ok")
            return redirect(url_for("site_admin.admin_awards_tracker", season=season_label))

    canonical = get_current_season()
    season = season_with_imported_data_fallback(db.session, canonical) if canonical else None
    default_season = season_display_label(season) if season else ""
    season_choices = season_label_choices_for_admin(db.session)
    if default_season and default_season not in season_choices:
        season_choices.insert(0, default_season)

    award_rows = list_history_awards_admin(db.session, season_filter=season_filter or None) if season_filter else []
    edit_award = db.session.get(HistoryAward, edit_award_id) if edit_award_id else None
    award_names = list(
        db.session.scalars(
            select(HistoryAward.award_name)
            .where(HistoryAward.award_name.is_not(None), HistoryAward.award_name != "")
            .distinct()
            .order_by(HistoryAward.award_name.asc())
        ).all()
    )
    if edit_award and edit_award.award_name:
        award_names.append(edit_award.award_name)
    award_choices = award_name_choices_from_names(award_names)
    edit_award_name = ""
    if edit_award and edit_award.award_name:
        edit_choices = award_name_choices_from_names([edit_award.award_name])
        edit_award_name = edit_choices[0] if edit_choices else ""

    all_star_first: dict[int, HistoryAllStar] = {}
    all_star_second: dict[int, HistoryAllStar] = {}
    if season_filter:
        all_star_first = all_stars_by_slot_for_season(db.session, season_filter, 1)
        all_star_second = all_stars_by_slot_for_season(db.session, season_filter, 2)

    gm_winner_choices: list[dict[str, object]] = []
    teams_by_id = {int(t.id): t for t in teams}
    for mem, gm_user in _active_trade_memberships(slug):
        if mem.team_id is None:
            continue
        tid = int(mem.team_id)
        tm = teams_by_id.get(tid)
        gm_winner_choices.append(
            {
                "user_id": int(gm_user.id),
                "team_id": tid,
                "label": f"{gm_display_name(gm_user)} — {tm.abbreviation if tm else tid}",
            }
        )
    gm_winner_choices.sort(key=lambda r: str(r.get("label") or "").lower())

    edit_gm_user_id = gm_user_id_from_award_notes(edit_award.notes) if edit_award else None

    return render_template(
        "admin_awards.html",
        league_slug=slug,
        season_filter=season_filter,
        default_season=default_season,
        season_choices=season_choices,
        award_rows=award_rows,
        award_choices=award_choices,
        edit_award=edit_award,
        edit_award_name=edit_award_name,
        edit_gm_user_id=edit_gm_user_id,
        all_star_first=all_star_first,
        all_star_second=all_star_second,
        slot_defaults=ALL_STAR_SLOT_DEFAULTS,
        players=players,
        teams=teams,
        gm_winner_choices=gm_winner_choices,
        staff_award_winner_admin_label=staff_award_winner_admin_label,
    )


site_admin_bp.add_url_rule(
    "/awards",
    endpoint="admin_awards",
    view_func=admin_awards_tracker,
    methods=["GET", "POST"],
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
            commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
                        "discord_channel_id": (request.form.get(f"discord_channel_id_{key}") or "").strip(),
                        "discord_channel_id_2": (request.form.get(f"discord_channel_id_2_{key}") or "").strip(),
                        "discord_channel_id_3": (request.form.get(f"discord_channel_id_3_{key}") or "").strip(),
                        "label": (request.form.get(f"label_{key}") or "").strip()[:120],
                        "description": (request.form.get(f"description_{key}") or "").strip()[:2000],
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
            commit_with_sqlite_retry(db.session)
            flash("Discord route settings updated.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "save_boxscore_team_channels":
            team_rows = []
            for key in request.form:
                if not str(key).startswith("boxscore_team_id_"):
                    continue
                raw_tid = (request.form.get(key) or "").strip()
                try:
                    tid = int(raw_tid)
                except (TypeError, ValueError):
                    continue
                team_rows.append(
                    {
                        "team_id": tid,
                        "discord_channel_id": (
                            request.form.get(f"boxscore_channel_id_{tid}") or ""
                        ).strip(),
                        "is_enabled": request.form.get(f"boxscore_enabled_{tid}") == "1",
                    }
                )
            saved_teams = update_game_boxscore_team_channels(
                db.session, slug, team_rows, int(current_user.id)
            )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_boxscore_team_channels_update",
                    detail_json=json.dumps({"rows": saved_teams}),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash("Team boxscore channel settings updated.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "save_bot_config":
            try:
                update_league_bot_config(
                    db.session,
                    league_slug=slug,
                    guild_id=(request.form.get("guild_id") or "").strip(),
                    is_enabled=request.form.get("bot_enabled") == "1",
                    notes=(request.form.get("bot_notes") or "").strip(),
                    updated_by_user_id=int(current_user.id),
                    gm_role_id=(request.form.get("gm_role_id") or "").strip(),
                )
            except ValueError as exc:
                flash(str(exc), "err")
                return redirect(url_for("site_admin.admin_discord_integration"))
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_bot_config_update",
                    detail_json=json.dumps(
                        {
                            "guild_id": (request.form.get("guild_id") or "").strip(),
                            "gm_role_id": (request.form.get("gm_role_id") or "").strip(),
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash("Discord bot connection settings saved.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "add_route":
            event_key = (request.form.get("new_event_key") or "").strip()
            channel_key = (request.form.get("new_channel_key") or "").strip()
            discord_channel_id = (request.form.get("new_discord_channel_id") or "").strip()
            label = (request.form.get("new_label") or "").strip()
            try:
                add_discord_route(
                    db.session,
                    league_slug=slug,
                    event_key=event_key,
                    channel_key=channel_key,
                    discord_channel_id=discord_channel_id,
                    label=label,
                    updated_by_user_id=int(current_user.id),
                )
            except ValueError as exc:
                flash(str(exc), "err")
                return redirect(url_for("site_admin.admin_discord_integration"))
            flash(f"Added route for '{event_key}'.", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "remove_route":
            event_key = (request.form.get("remove_event_key") or "").strip()
            if not delete_discord_route(db.session, league_slug=slug, event_key=event_key):
                flash("Route not found.", "err")
            else:
                flash(f"Removed route '{event_key}'.", "ok")
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
            commit_with_sqlite_retry(db.session)
            if created is None:
                flash("Test event skipped: route is disabled or unavailable for that event key.", "err")
            else:
                flash(f"Test event queued for '{event_key}'.", "ok")
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
            commit_with_sqlite_retry(db.session)
            flash(f"Replayed {replayed} dead-letter event(s).", "ok")
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "fresh_playoff_bracket":
            from app.services.playoff_discord_bracket import enqueue_fresh_playoff_bracket_discord

            queued = enqueue_fresh_playoff_bracket_discord(db.session, db.session, slug)
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_fresh_playoff_bracket",
                    detail_json=json.dumps({"queued": bool(queued)}),
                )
            )
            commit_with_sqlite_retry(db.session)
            if queued:
                flash("Queued fresh playoff bracket Discord posts.", "ok")
            else:
                flash(
                    "Could not queue playoff bracket posts. Enable playoff_bracket_update, "
                    "set the #playoff-bracket channel ID, and ensure playoffs have started.",
                    "err",
                )
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "queue_recent_boxscores":
            from app.services.game_boxscore_discord import queue_recent_game_boxscores

            raw_days = (request.form.get("boxscore_days") or "7").strip()
            try:
                boxscore_days = int(raw_days)
            except (TypeError, ValueError):
                boxscore_days = 7
            boxscore_days = max(1, min(boxscore_days, 60))
            boxscore_force = (request.form.get("boxscore_force") or "").strip() in {
                "1",
                "true",
                "on",
                "yes",
            }
            box_stats = queue_recent_game_boxscores(
                db.session,
                db.session,
                league_slug=slug,
                days=boxscore_days,
                created_by_user_id=int(current_user.id),
                force=boxscore_force,
            )
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_queue_recent_boxscores",
                    detail_json=json.dumps(
                        {
                            "days": boxscore_days,
                            "force": bool(boxscore_force),
                            "games": int(box_stats.get("games") or 0),
                            "queued": int(box_stats.get("queued") or 0),
                            "skipped": int(box_stats.get("skipped") or 0),
                            "delivered_cleared": int(box_stats.get("delivered_cleared") or 0),
                            "outbound_cancelled": int(box_stats.get("outbound_cancelled") or 0),
                            "window_start": box_stats.get("window_start"),
                            "window_end": box_stats.get("window_end"),
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash(
                str(box_stats.get("message") or "Boxscore queue finished."),
                "ok" if box_stats.get("ok") else "err",
            )
            return redirect(url_for("site_admin.admin_discord_integration"))
        if action == "force_start_live_sim_cycle":
            from app.services.sim_cycle_discord import force_start_live_sim_cycle

            ok, message = force_start_live_sim_cycle(db.session, db.session, slug)
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="discord_force_start_live_sim_cycle",
                    detail_json=json.dumps({"queued": bool(ok)}),
                )
            )
            commit_with_sqlite_retry(db.session)
            flash(message, "ok" if ok else "err")
            return redirect(url_for("site_admin.admin_discord_integration"))
    status = (request.args.get("status") or "").strip().lower()
    event_key_filter = (request.args.get("event_key") or "").strip()
    routes = list_discord_routes(db.session, slug)
    boxscore_team_channels = list_game_boxscore_team_channels(db.session, db.session, slug)
    bot_config = get_league_bot_config(db.session, slug)
    events = list_outbound_events(
        db.session, league_slug=slug, status=status, event_key=event_key_filter, limit=250
    )
    dead_letters = list_outbound_events(db.session, league_slug=slug, status="failed", limit=50)
    dm_events = list(
        db.session.scalars(
            select(DiscordDirectMessageEvent)
            .where(DiscordDirectMessageEvent.league_slug == slug)
            .order_by(DiscordDirectMessageEvent.created_at.desc())
            .limit(100)
        ).all()
    )
    dm_dead_letters = [e for e in dm_events if e.status == "failed"][:50]
    prune_obsolete_discord_bot_heartbeats(db.session, league_slug=slug)
    heartbeats = list_heartbeats(db.session, league_slug=slug, limit=10)
    from app.services.sim_cycle_discord import sim_cycle_tracker_route_ready, sim_log_route_ready
    from app.site_models import SimCycleState

    sim_log_ready = sim_log_route_ready(db.session, slug)
    sim_tracker_ready = sim_cycle_tracker_route_ready(db.session, slug)
    sim_cycle_state = db.session.scalar(
        select(SimCycleState).where(SimCycleState.league_slug == slug).limit(1)
    )
    sim_cycle_phase = str(getattr(sim_cycle_state, "phase", None) or "idle")
    expected_bot_name = canonical_discord_bot_name()
    secret_set = bool(str(current_app.config.get("DISCORD_EVENTS_SHARED_SECRET") or "").strip())
    now = datetime.utcnow()
    queue_recent_ok = any(
        e.created_at and (now - e.created_at) <= timedelta(minutes=5) for e in events[:100]
    )
    heartbeat_rows = [
        {
            "bot_name": str(h.bot_name or expected_bot_name),
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
        boxscore_team_channels=boxscore_team_channels,
        bot_config=bot_config,
        events=events,
        dead_letters=dead_letters,
        dm_events=dm_events,
        dm_dead_letters=dm_dead_letters,
        selected_status=status,
        selected_event_key=event_key_filter,
        secret_set=secret_set,
        queue_recent_ok=queue_recent_ok,
        heartbeat_rows=heartbeat_rows,
        expected_bot_name=expected_bot_name,
        sim_log_ready=sim_log_ready,
        sim_tracker_ready=sim_tracker_ready,
        sim_cycle_phase=sim_cycle_phase,
        site_public_base_url=str(current_app.config.get("SITE_PUBLIC_BASE_URL") or "").strip().rstrip("/"),
        fanout_event_keys=DISCORD_CHANNEL_FANOUT_EVENT_KEYS,
        is_racing_league=is_racing_league(slug),
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
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
    flash("Event cancelled.", "ok")
    return redirect(url_for("site_admin.admin_discord_integration"))


@site_admin_bp.post("/discord-dms/<int:eid>/requeue")
@login_required
def admin_discord_dm_requeue(eid: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    slug = _league_slug()
    row = db.session.get(DiscordDirectMessageEvent, eid)
    if not row or row.league_slug != slug:
        abort(404)
    row.status = "pending"
    row.last_error = ""
    row.next_attempt_at = None
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="discord_dm_requeue",
            detail_json=json.dumps({"event_id": int(row.id), "event_key": str(row.event_key or "")}),
        )
    )
    commit_with_sqlite_retry(db.session)
    flash("DM event requeued.", "ok")
    return redirect(url_for("site_admin.admin_discord_integration"))


@site_admin_bp.post("/discord-dms/<int:eid>/cancel")
@login_required
def admin_discord_dm_cancel(eid: int):
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER, ADMIN_ROLE_CONTENT)
    slug = _league_slug()
    row = db.session.get(DiscordDirectMessageEvent, eid)
    if not row or row.league_slug != slug:
        abort(404)
    row.status = "cancelled"
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=slug,
            action="discord_dm_cancel",
            detail_json=json.dumps({"event_id": int(row.id), "event_key": str(row.event_key or "")}),
        )
    )
    commit_with_sqlite_retry(db.session)
    flash("DM event cancelled.", "ok")
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
        ann_body = str(ann.body or "")
        _enqueue_discord_event(
            "announcement_posted",
            {
                "announcement_id": int(ann.id),
                "title": str(ann.title or ""),
                "level": str(ann.level or "info"),
                "body": ann_body,
                "body_preview": ann_body[:280],
                "has_image": False,
                "url": build_league_public_url(slug, "/"),
            },
            source_type="announcement",
            source_id=int(ann.id),
        )
        commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
        commit_with_sqlite_retry(db.session)
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
        commit_with_sqlite_retry(db.session)
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
        league_wide = raw_tid.lower() == "league"
        team = None
        team_id: int | None = None
        if not league_wide:
            if not raw_tid.isdigit():
                flash("Select a team this article is about, or League.", "err")
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
        image_payload: tuple[str, bytes] | None = None
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
            from app.services.news_article_media import _MAX_BYTES

            image_data = upload.read(_MAX_BYTES + 1)
            if len(image_data) > _MAX_BYTES:
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
            image_payload = (upload.filename, image_data)

        def _publish_admin_news_article() -> NewsArticle:
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
            if image_payload is not None:
                from io import BytesIO

                from werkzeug.datastructures import FileStorage

                from app.services.news_article_media import save_news_article_image

                filename, data = image_payload
                rel = save_news_article_image(
                    FileStorage(stream=BytesIO(data), filename=filename),
                    league_slug=slug,
                    article_id=art.id,
                )
                if not rel:
                    raise ValueError("image_save_failed")
                art.image_rel_path = rel
            return art

        try:
            art = write_with_sqlite_retry(db.session, _publish_admin_news_article)
        except ValueError as exc:
            if str(exc) == "image_save_failed":
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
            raise
        if league_wide:
            discord_team_fields: dict = {
                "league_wide": True,
                "team_name": str(
                    current_app.config.get("LEAGUE_DISPLAY_NAME") or "League"
                ),
            }
            role_mention = gm_role_mention_for_league(db.session, slug)
            if role_mention.startswith("<@"):
                discord_team_fields["team_gm_mention"] = role_mention
        else:
            discord_team_fields = team_fields_for_discord(team)
        _enqueue_discord_event(
            "admin_news_published",
            news_article_discord_payload(
                art,
                category=str(art.category or ""),
                url=build_news_article_public_url(slug, art.id),
                published_at_utc=art.published_at.isoformat(timespec="seconds")
                if art.published_at
                else "",
                **discord_team_fields,
            ),
            source_type="news_article",
            source_id=int(art.id),
        )
        commit_with_sqlite_retry(db.session)
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


def _admin_news_logo_season_year() -> int | None:
    """Use the active league timeline for admin news row logos."""
    canonical = get_current_season()
    season = season_with_imported_data_fallback(db.session, canonical) if canonical else None
    if season is None:
        season = canonical
    try:
        return int(season.start_year) if season and season.start_year is not None else None
    except (TypeError, ValueError):
        return None


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
    logo_season_year = _admin_news_logo_season_year()
    news_team_logo_url_by_article_id = {
        int(a.id): dashboard_team_logo_url(news_teams_by_id.get(int(a.team_id)), logo_season_year)
        for a in rows
        if a.team_id and int(a.team_id) in news_teams_by_id
    }
    return render_template(
        "admin_news_queue.html",
        articles=rows,
        news_authors_by_id=news_authors_by_id,
        news_teams_by_id=news_teams_by_id,
        news_team_logo_url_by_article_id=news_team_logo_url_by_article_id,
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
    logo_season_year = _admin_news_logo_season_year()
    return render_template(
        "admin_news_preview.html",
        article=art,
        author=author,
        team=team,
        team_logo_src=dashboard_team_logo_url(team, logo_season_year) if team else "",
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
    db.session.refresh(art)
    team = resolve_news_article_team(db.session, art)
    _enqueue_discord_event(
        "gm_news_published",
        news_article_discord_payload(
            art,
            category=str(art.category or ""),
            url=build_news_article_public_url(slug, art.id),
            published_at_utc=art.published_at.isoformat(timespec="seconds")
            if art.published_at
            else "",
            **team_fields_for_discord(team),
        ),
        source_type="news_article",
        source_id=int(art.id),
    )
    commit_with_sqlite_retry(db.session)
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
    commit_with_sqlite_retry(db.session)
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
    export_date = parse_export_date(request.form.get("export_date"))
    raw = request.form.getlist("team_slug")
    team_slugs = list(dict.fromkeys(s.strip() for s in raw if s and s.strip()))
    if not team_slugs:
        flash("Select at least one team.", "err")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    label = league_display_name(cur_slug)
    ap_allowed = True
    ap_block_message = ""
    if not dry_run:
        pe = evaluate_points_economy_mutations_allowed(db.session, cur_slug)
        if not pe.allowed:
            ap_allowed = False
            ap_block_message = pe.message
    note = f"EXPORT: +1 AP ({label})"
    if dry_run:
        matched_slugs: list[str] = []
        ap_added = 0
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
            ap_added += 1
        sample = ", ".join(matched_slugs[:8]) if matched_slugs else "none"
        if len(matched_slugs) > 8:
            sample += ", …"
        flash(
            f"[DRY RUN] EXPORT would add {ap_added} ledger row(s) (+1 AP) and register "
            f"{ap_added} attendance row(s) for {export_date.isoformat()} in {label}. "
            f"Teams: {sample}",
            "ok",
        )
        return redirect(url_for("site_admin.admin_ap_ledger"))

    def _apply_export_multileague() -> dict[str, object]:
        ap_added = 0
        attendance_added = 0
        warnings_sent = 0
        matched_slugs: list[str] = []
        pending: list[tuple[int, str, object | None]] = []
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
            ledger_row = None
            if ap_allowed:
                ledger_row = add_ledger_entry(
                    league_slug=cur_slug,
                    team_id=tid,
                    delta=1,
                    reason_code="manual",
                    meta={
                        "note": note,
                        "team_slug": team_slug,
                        "export_date": export_date.isoformat(),
                    },
                    created_by_user_id=current_user.id,
                    source_ref=f"manual_export:{cur_slug}:{tid}:{export_date.isoformat()}",
                )
            pending.append((int(tid), team_slug, ledger_row))
        db.session.flush()
        for tid, _team_slug, ledger_row in pending:
            if ledger_row is not None:
                ap_added += 1
            ap_ledger_entry_id = None
            if ledger_row is not None and getattr(ledger_row, "id", None) is not None:
                ap_ledger_entry_id = int(ledger_row.id)
            attendance_row, created_new = register_export_attendance(
                db.session,
                league_slug=cur_slug,
                team_id=int(tid),
                export_date=export_date,
                checked_by_user_id=int(current_user.id),
                ap_ledger_entry_id=ap_ledger_entry_id,
                flush=False,
            )
            if created_new:
                attendance_added += 1
            if maybe_send_export_gap_warning(
                db.session,
                attendance_row=attendance_row,
                league_slug=cur_slug,
                admin_user_id=int(current_user.id),
            ):
                warnings_sent += 1
        db.session.flush()
        return {
            "ap_added": ap_added,
            "attendance_added": attendance_added,
            "warnings_sent": warnings_sent,
            "matched_slugs": matched_slugs,
        }

    result = write_with_sqlite_retry(db.session, _apply_export_multileague)
    ap_added = int(result.get("ap_added") or 0)
    attendance_added = int(result.get("attendance_added") or 0)
    warnings_sent = int(result.get("warnings_sent") or 0)
    matched_slugs = list(result.get("matched_slugs") or [])
    if matched_slugs:
        parts = [
            f"EXPORT {export_date.isoformat()}: registered attendance for {len(matched_slugs)} team(s) in {label}."
        ]
        if ap_allowed:
            parts.append(f"Added {ap_added} AP ledger row(s).")
        else:
            parts.append(f"AP not awarded ({ap_block_message})")
        if warnings_sent:
            parts.append(f"Sent {warnings_sent} export-gap warning(s).")
        flash(" ".join(parts), "ok" if attendance_added or ap_added or matched_slugs else "err")
    else:
        flash(
            f"No matching teams in this league ({label}) for the selection.",
            "err",
        )
    if matched_slugs:
        try:
            from app.services.sim_cycle_discord import handle_sim_cycle_after_admin_export
            from app.sqlite_retry import commit_with_sqlite_retry

            handle_sim_cycle_after_admin_export(
                db.session, db.session, cur_slug, export_date
            )
            commit_with_sqlite_retry(db.session)
        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "sim cycle close enqueue failed after export for %s", cur_slug
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
    team_id_by_slug = {
        str(t.slug or "").strip().casefold(): int(t.id)
        for t in teams
        if str(t.slug or "").strip()
    }
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
            tid = team_id_by_slug.get(team_slug.casefold())
            if tid is None:
                continue
            preview.append((team_slug, 1))
            if dry_run:
                entries += 1
                continue
        if dry_run:
            show = ", ".join([f"{s}: +1" for s, _d in preview[:8]]) if preview else "none"
            if len(preview) > 8:
                show += ", …"
            flash(
                f"[DRY RUN] PREDICTIONS would add {entries} ledger row(s) in {league_name}. {show}",
                "ok",
            )
            return redirect(url_for("site_admin.admin_ap_ledger"))

        def _apply_predictions_batch() -> int:
            count = 0
            for team_slug in picked:
                if team_slug not in allowed_slugs:
                    continue
                tid = team_id_by_slug.get(team_slug.casefold())
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
                count += 1
            return count

        entries = write_with_sqlite_retry(db.session, _apply_predictions_batch)
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
    pending_rows: list[tuple[str, int, int]] = []
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
        tid = team_id_by_slug.get(team_slug.casefold())
        if tid is None:
            continue
        preview_rows.append((team_slug, int(delta)))
        pending_rows.append((team_slug, int(tid), int(delta)))
        if dry_run:
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

    def _apply_batch_adjustments() -> int:
        count = 0
        for team_slug, tid, delta in pending_rows:
            add_ledger_entry(
                league_slug=cur_slug,
                team_id=tid,
                delta=delta,
                reason_code=reason,
                meta={"batch": label, "team_slug": team_slug},
                created_by_user_id=current_user.id,
            )
            count += 1
        return count

    entries = write_with_sqlite_retry(db.session, _apply_batch_adjustments)
    if entries:
        flash(
            f"{label}: wrote {entries} ledger row(s) in {league_name} only "
            f"(non-zero inputs; team slugs as shown on this page).",
            "ok",
        )
    else:
        flash(f"{label}: enter at least one non-zero amount.", "err")
    return redirect(url_for("site_admin.admin_ap_ledger"))


@site_admin_bp.route("/staff-budgets", methods=["GET", "POST"])
@login_required
def admin_staff_budgets():
    """Enter per-team staff salary budgets for the current season."""
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    start_year = current_season_start_year(db.session)
    if start_year is None:
        flash("No current season is configured for this league.", "err")
        return redirect(url_for("site_admin.admin_home"))

    if request.method == "POST":
        teams = main_league_teams(db.session)
        for t in teams:
            tid = int(t.id)
            raw_budget = (request.form.get(f"budget_{tid}") or "").strip().replace(",", "").replace("$", "")
            try:
                budget_amount = max(0, int(raw_budget)) if raw_budget else 0
            except ValueError:
                budget_amount = 0
            row = db.session.scalar(
                select(TeamStaffBudget).where(
                    TeamStaffBudget.league_slug == slug,
                    TeamStaffBudget.season_start_year == int(start_year),
                    TeamStaffBudget.team_id == tid,
                ).limit(1)
            )
            if row is None:
                row = TeamStaffBudget(
                    league_slug=slug,
                    season_start_year=int(start_year),
                    team_id=tid,
                    budget_amount=budget_amount,
                    current_salary_amount=0,
                    updated_by_user_id=int(current_user.id),
                )
                db.session.add(row)
            else:
                row.budget_amount = budget_amount
                row.updated_by_user_id = int(current_user.id)

            raw_penalty = (
                request.form.get(f"penalty_{tid}") or ""
            ).strip().replace(",", "").replace("$", "")
            try:
                penalty_amount = max(0, int(raw_penalty)) if raw_penalty else 0
            except ValueError:
                penalty_amount = 0
            admin_set_team_staff_penalty_total(
                db.session,
                league_slug=slug,
                season_start_year=int(start_year),
                team_id=tid,
                penalty_amount=penalty_amount,
                admin_user_id=int(current_user.id),
            )
        commit_with_sqlite_retry(db.session)
        flash("Staff salary budgets and penalties saved.", "ok")
        return redirect(url_for("site_admin.admin_staff_budgets"))

    ctx = staff_salary_context(db.session, league_slug=slug)
    return render_template("admin_staff_budgets.html", **ctx)


@site_admin_bp.post("/team-staff/<team_slug>/contract")
@login_required
def admin_team_staff_contract(team_slug: str):
    """Save staff contract overlay from team Staff tab."""
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    team = db.session.scalar(select(Team).where(Team.slug == team_slug).limit(1))
    if not team:
        abort(404)
    start_year = current_season_start_year(db.session)
    if start_year is None:
        flash("No current season configured.", "err")
        return redirect(url_for("main.team_page", slug=team_slug, panel="staff"))
    try:
        annual_salary = int(
            str(request.form.get("annual_salary") or "0").replace(",", "").replace("$", "")
        )
        contract_years = int(request.form.get("contract_years") or "1")
    except ValueError:
        flash("Invalid salary or contract term.", "err")
        return redirect(url_for("main.team_page", slug=team_slug, panel="staff"))
    result = admin_save_staff_contract(
        db.session,
        league_slug=slug,
        season_start_year=int(start_year),
        team_id=int(team.id),
        staff_fhm_id=(request.form.get("staff_fhm_id") or "").strip(),
        role=(request.form.get("role") or "").strip(),
        annual_salary=annual_salary,
        contract_years=contract_years,
        fhm_team_id=getattr(team, "fhm_team_id", None),
    )
    commit_with_sqlite_retry(db.session)
    flash(result.message, "ok" if result.ok else "err")
    return redirect(url_for("main.team_page", slug=team_slug, panel="staff"))


@site_admin_bp.post("/team-staff/<team_slug>/retire")
@login_required
def admin_team_staff_retire(team_slug: str):
    """Retire staff from team Staff tab."""
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    team = db.session.scalar(select(Team).where(Team.slug == team_slug).limit(1))
    if not team:
        abort(404)
    start_year = current_season_start_year(db.session)
    if start_year is None:
        flash("No current season configured.", "err")
        return redirect(url_for("main.team_page", slug=team_slug, panel="staff"))
    result = admin_retire_staff(
        db.session,
        league_slug=slug,
        season_start_year=int(start_year),
        team_id=int(team.id),
        staff_fhm_id=(request.form.get("staff_fhm_id") or "").strip(),
    )
    if result.ok and result.entry:
        _publish_admin_staff_transaction(
            slug=slug, team=team, entry=result.entry, action="retire"
        )
    commit_with_sqlite_retry(db.session)
    flash(result.message, "ok" if result.ok else "err")
    return redirect(url_for("main.team_page", slug=team_slug, panel="staff"))


@site_admin_bp.post("/team-staff/<team_slug>/fire")
@login_required
def admin_team_staff_fire(team_slug: str):
    """Fire staff from team Staff tab, with optional salary penalty."""
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    team = db.session.scalar(select(Team).where(Team.slug == team_slug).limit(1))
    if not team:
        abort(404)
    start_year = current_season_start_year(db.session)
    if start_year is None:
        flash("No current season configured.", "err")
        return redirect(url_for("main.team_page", slug=team_slug, panel="staff"))
    try:
        penalty_amount = int(
            str(request.form.get("penalty_amount") or "0")
            .replace(",", "")
            .replace("$", "")
        )
    except ValueError:
        penalty_amount = 0
    result = admin_fire_staff(
        db.session,
        league_slug=slug,
        season_start_year=int(start_year),
        team_id=int(team.id),
        admin_user_id=int(current_user.id),
        staff_fhm_id=(request.form.get("staff_fhm_id") or "").strip(),
        penalty_amount=penalty_amount,
    )
    if result.ok and result.entry:
        _publish_admin_staff_transaction(
            slug=slug, team=team, entry=result.entry, action="fire"
        )
    commit_with_sqlite_retry(db.session)
    flash(result.message, "ok" if result.ok else "err")
    return redirect(url_for("main.team_page", slug=team_slug, panel="staff"))


@site_admin_bp.route("/cap-penalties", methods=["GET", "POST"])
@login_required
def admin_cap_penalties():
    """Enter manual FHM-style cap hit penalties per team for the current season."""
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    start_year = current_season_start_year(db.session)
    if start_year is None:
        flash("No current season is configured for this league.", "err")
        return redirect(url_for("site_admin.admin_home"))

    if request.method == "POST":
        teams = main_league_teams(db.session)
        for t in teams:
            tid = int(t.id)
            raw = (request.form.get(f"penalty_{tid}") or "").strip().replace(",", "").replace("$", "")
            try:
                amount = max(0, int(raw)) if raw else 0
            except ValueError:
                amount = 0
            row = db.session.scalar(
                select(TeamCapPenalty).where(
                    TeamCapPenalty.league_slug == slug,
                    TeamCapPenalty.season_start_year == int(start_year),
                    TeamCapPenalty.team_id == tid,
                ).limit(1)
            )
            if row is None:
                row = TeamCapPenalty(
                    league_slug=slug,
                    season_start_year=int(start_year),
                    team_id=tid,
                    penalty_amount=amount,
                    updated_by_user_id=int(current_user.id),
                )
                db.session.add(row)
            else:
                row.penalty_amount = amount
                row.updated_by_user_id = int(current_user.id)
        commit_with_sqlite_retry(db.session)
        flash("Cap hit penalties saved.", "ok")
        return redirect(url_for("site_admin.admin_cap_penalties"))

    ctx = cap_penalty_admin_context(db.session, league_slug=slug)
    return render_template("admin_cap_penalties.html", **ctx)


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
            def _add_manual_ledger_row() -> None:
                add_ledger_entry(
                    league_slug=slug,
                    team_id=tid,
                    delta=delta,
                    reason_code="manual",
                    meta={"note": note},
                    created_by_user_id=current_user.id,
                )

            write_with_sqlite_retry(db.session, _add_manual_ledger_row)
            flash("Ledger entry added.", "ok")
        return redirect(url_for("site_admin.admin_ap_ledger"))
    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    team_rows = [{"team": t, "balance": team_ap_balance(slug, t.id)} for t in teams]
    team_rows.sort(key=lambda r: (r["team"].name or "").lower())
    return render_template(
        "admin_ap_ledger.html",
        teams=teams,
        team_rows=team_rows,
        today_iso=datetime.utcnow().date().isoformat(),
        **_ap_ledger_template_context(
            slug,
            teams=teams,
            form_endpoint="site_admin.admin_ap_ledger",
        ),
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
    from app.services.ap_service import (
        ap_redemption_party_display,
        load_ap_redemption_parties,
        parse_redemption_line_labels,
    )

    teams_by_id, users_by_id = load_ap_redemption_parties(db.session, list(rows))
    queue_rows: list[dict] = []
    for r in rows:
        party = ap_redemption_party_display(
            r,
            team=teams_by_id.get(int(r.team_id)),
            user=users_by_id.get(int(r.user_id)),
        )
        titles = parse_redemption_line_labels(r.lines_json or "[]")
        queue_rows.append(
            {
                "req": r,
                "team": teams_by_id.get(int(r.team_id)),
                "gm_name": party["gm_name"],
                "team_name": party["team_name"],
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
    from app.services.ap_service import ap_redemption_party_display

    team = db.session.get(Team, int(req.team_id))
    user = db.session.get(User, int(req.user_id))
    party = ap_redemption_party_display(req, team=team, user=user)
    line_rows: list[dict] = []
    try:
        from app.services.ap_redemption_forms import line_item_display_title

        parsed = json.loads(req.lines_json or "[]")
        if isinstance(parsed, list):
            for it in parsed:
                if not isinstance(it, dict):
                    continue
                title = str(it.get("title") or "").strip()
                details = it.get("details")
                line_rows.append(
                    {
                        "title": title,
                        "cost": it.get("cost"),
                        "summary": str(it.get("summary") or "").strip()
                        or line_item_display_title(
                            title, details if isinstance(details, dict) else None
                        ),
                        "details": details if isinstance(details, dict) else {},
                    }
                )
    except Exception:
        pass
    return render_template(
        "admin_ap_request_detail.html",
        req=req,
        line_rows=line_rows,
        team=team,
        gm_name=party["gm_name"],
        team_name=party["team_name"],
        gm_email=party["gm_email"],
        balance=team_ap_balance(slug, int(req.team_id)),
    )


@site_admin_bp.post("/ap-requests/<int:rid>/approve")
@login_required
def admin_ap_approve(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    req = db.session.get(ApRedemptionRequest, rid)
    if not req or req.league_slug != slug:
        abort(404)
    if req.status != "pending":
        flash(f"Request #{req.id} has already been {req.status}.", "warn")
        return redirect(url_for("site_admin.admin_ap_requests"))
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
        gm_user = db.session.get(User, int(req.user_id))
        body_parts = []
        if isinstance(line_items, list):
            from app.services.ap_redemption_forms import line_item_display_title

            for it in line_items:
                title = str((it or {}).get("title") or "").strip()
                cost = (it or {}).get("cost")
                details = (it or {}).get("details")
                if title:
                    label = line_item_display_title(
                        title, details if isinstance(details, dict) else None
                    )
                    body_parts.append(f"- {label}" + (f" ({cost} AP)" if cost is not None else ""))
        red_label = ", ".join([p.replace("- ", "", 1) for p in body_parts]) if body_parts else f"Request #{req.id}"
        art = NewsArticle(
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
        db.session.add(art)
        flush_with_sqlite_retry(db.session)
        commit_with_sqlite_retry(db.session)
        _enqueue_discord_event(
            "ap_redemption_posted",
            news_article_discord_payload(
                art,
                request_id=int(req.id),
                total_cost=int(req.total_cost),
                redemption_label=red_label,
                gm_name=gm_discord_name(gm_user),
                url=build_news_article_public_url(slug, art.id),
                **team_fields_for_discord(team),
            ),
            source_type="ap_redemption",
            source_id=int(req.id),
        )
        commit_with_sqlite_retry(db.session)
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
    if not req or req.league_slug != slug:
        abort(404)
    if req.status != "pending":
        flash(f"Request #{req.id} has already been {req.status}.", "warn")
        return redirect(url_for("site_admin.admin_ap_requests"))
    note = (request.form.get("admin_note") or "").strip()
    if not note:
        flash("A deny reason is required.", "err")
        return redirect(url_for("site_admin.ap_request_one", rid=req.id))
    req.admin_note = note[:4000]
    req.status = "denied"
    req.processed_at = datetime.utcnow()
    commit_with_sqlite_retry(db.session)
    notify_redemption_denied(slug, req)
    flash("Request denied and GM notified in-app.", "ok")
    return redirect(url_for("site_admin.admin_ap_requests"))


@site_admin_bp.get("/staff-requests")
@login_required
def admin_staff_requests():
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    flash("Staff hire/fire requests are retired. Use Staff Hire/Fire in the league office.", "warn")
    return redirect(url_for("site_gm.staff_salaries_page"))


@site_admin_bp.get("/staff-requests/<int:rid>")
@login_required
def admin_staff_request_one(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    flash("Staff hire/fire requests are retired. Use Staff Hire/Fire in the league office.", "warn")
    return redirect(url_for("site_gm.staff_salaries_page"))


@site_admin_bp.post("/staff-requests/<int:rid>/approve")
@login_required
def admin_staff_approve(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    flash("Staff hire/fire requests are retired. Use Staff Hire/Fire in the league office.", "warn")
    return redirect(url_for("site_gm.staff_salaries_page"))


@site_admin_bp.post("/staff-requests/<int:rid>/deny")
@login_required
def admin_staff_deny(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    flash("Staff hire/fire requests are retired. Use Staff Hire/Fire in the league office.", "warn")
    return redirect(url_for("site_gm.staff_salaries_page"))


@site_admin_bp.route("/rfa-offers", methods=["GET", "POST"])
@login_required
def admin_rfa_offers():
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    if request.method == "POST":
        action = (request.form.get("action") or "").strip().lower()
        if action == "save_cap_panel":
            try:
                panel_id = int(request.form.get("panel_id") or "0")
                raw_ceiling = (request.form.get("cap_ceiling") or "").strip().replace(",", "").replace("$", "")
                raw_floor = (request.form.get("cap_floor") or "").strip().replace(",", "").replace("$", "")
                cap_ceiling = int(raw_ceiling) if raw_ceiling else None
                cap_floor = int(raw_floor) if raw_floor else None
            except ValueError:
                flash("Cap ceiling and floor must be valid dollar amounts.", "err")
                return redirect(url_for("site_admin.admin_rfa_offers"))
            saved = save_salary_cap_panel(
                db.session,
                league_slug=slug,
                panel_id=panel_id,
                cap_ceiling=cap_ceiling,
                cap_floor=cap_floor,
            )
            if saved is None:
                flash("Cap panel not found.", "err")
            else:
                from app.services.salary_cap_schedule import season_label_from_start_year

                label = season_label_from_start_year(int(saved.season_start_year))
                flash(f"Saved {label} salary cap schedule.", "ok")
            return redirect(url_for("site_admin.admin_rfa_offers"))
        flash("Unknown action.", "err")
        return redirect(url_for("site_admin.admin_rfa_offers"))

    cap_panels_view = build_cap_panels_view(
        db.session,
        db.session,
        league_slug=slug,
        active_count=3,
    )
    season = get_current_season()
    season_start_year = int(season.start_year) if season and season.start_year is not None else None
    current_cap_ceiling = None
    if season_start_year is not None:
        current_cap_ceiling, _ = cap_for_season(db.session, slug, season_start_year)
    comp_reference_rows = compensation_reference_rows(
        int(current_cap_ceiling) if current_cap_ceiling else None
    )
    rows = db.session.scalars(
        select(RfaOfferRequest)
        .where(
            RfaOfferRequest.league_slug == slug,
            RfaOfferRequest.status.in_(
                ("pending_admin", "awaiting_equalization", "awaiting_original_match")
            ),
        )
        .order_by(RfaOfferRequest.created_at.desc())
    ).all()
    player_ids = {int(r.player_id) for r in rows}
    team_ids = {int(r.offering_team_id) for r in rows} | {int(r.rights_team_id) for r in rows}
    players_by_id = {
        int(p.id): p
        for p in db.session.scalars(select(Player).where(Player.id.in_(player_ids))).all()
    } if player_ids else {}
    teams_by_id = {
        int(t.id): t for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all()
    } if team_ids else {}
    user_ids = {int(r.offering_user_id) for r in rows}
    users_by_id = {
        int(u.id): u for u in db.session.scalars(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}
    queue_rows = [
        {
            "req": r,
            "player": players_by_id.get(int(r.player_id)),
            "offering_team": teams_by_id.get(int(r.offering_team_id)),
            "rights_team": teams_by_id.get(int(r.rights_team_id)),
            "user": users_by_id.get(int(r.offering_user_id)),
            "category_label": CATEGORY_LABELS.get(r.rfa_category, r.rfa_category),
        }
        for r in rows
    ]
    return render_template(
        "admin_rfa_offers.html",
        queue_rows=queue_rows,
        status_label=status_label,
        cap_panels_view=cap_panels_view,
        comp_reference_rows=comp_reference_rows,
        current_cap_ceiling=current_cap_ceiling,
    )


@site_admin_bp.get("/rfa-offers/<int:rid>")
@login_required
def admin_rfa_offer_one(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    req = db.session.get(RfaOfferRequest, rid)
    if not req or req.league_slug != slug:
        abort(404)
    player = db.session.get(Player, int(req.player_id))
    offering_team = db.session.get(Team, int(req.offering_team_id))
    rights_team = db.session.get(Team, int(req.rights_team_id))
    offering_user = db.session.get(User, int(req.offering_user_id))
    return render_template(
        "admin_rfa_offer_detail.html",
        req=req,
        player=player,
        offering_team=offering_team,
        rights_team=rights_team,
        offering_user=offering_user,
        category_labels=CATEGORY_LABELS,
        category_tooltips=CATEGORY_TOOLTIPS,
        happiness_levels=HAPPINESS_LEVELS,
        happiness_label=happiness_label,
        status_label=status_label,
    )


@site_admin_bp.post("/rfa-offers/<int:rid>/happiness")
@login_required
def admin_rfa_set_happiness(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    req = db.session.get(RfaOfferRequest, rid)
    if not req or req.league_slug != slug or req.status != "pending_admin":
        abort(404)
    happiness = (request.form.get("happiness") or "").strip().lower()
    if happiness not in HAPPINESS_LEVELS:
        flash("Choose a happiness level.", "err")
        return redirect(url_for("site_admin.admin_rfa_offer_one", rid=rid))
    req.happiness = happiness
    commit_with_sqlite_retry(db.session)
    flash(f"Happiness set to {happiness_label(happiness)}.", "ok")
    return redirect(url_for("site_admin.admin_rfa_offer_one", rid=rid))


@site_admin_bp.post("/rfa-offers/<int:rid>/player-decision")
@login_required
def admin_rfa_player_decision(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    req = db.session.get(RfaOfferRequest, rid)
    if not req or req.league_slug != slug or req.status != "pending_admin":
        abort(404)
    if not req.happiness:
        flash("Set player happiness before rolling the decision.", "err")
        return redirect(url_for("site_admin.admin_rfa_offer_one", rid=rid))
    player = db.session.get(Player, int(req.player_id))
    accepted, roll = roll_player_accepts(req.rfa_category, str(req.happiness))
    req.player_decision_roll = float(roll)
    req.player_accepted = bool(accepted)
    req.processed_by_user_id = int(current_user.id)
    req.processed_at = datetime.utcnow()
    if not accepted:
        req.status = "player_rejected"
        commit_with_sqlite_retry(db.session)
        notify_rfa_player_rejected(slug, req, player=player)
        flash(f"Player rejected the offer (roll {roll:.1f}). Offering GM notified.", "ok")
        return redirect(url_for("site_admin.admin_rfa_offers"))
    if req.rfa_category == "group_i":
        req.status = "awaiting_equalization"
        commit_with_sqlite_retry(db.session)
        notify_rfa_awaiting_equalization(slug, req, player=player)
        flash("Player accepted — both GMs notified to submit equalization trade.", "ok")
        return redirect(url_for("site_admin.admin_rfa_offers"))
    if req.rfa_category == "group_ii":
        req.status = "awaiting_original_match"
        commit_with_sqlite_retry(db.session)
        notify_rfa_awaiting_match(slug, req, player=player)
        flash("Player accepted — original team GM notified for match/reject.", "ok")
        return redirect(url_for("site_admin.admin_rfa_offers"))
    if req.rfa_category == "group_iii":
        allows_match, match_roll = roll_group_iii_allows_match(str(req.happiness))
        req.group_iii_allows_match = bool(allows_match)
        if allows_match:
            req.status = "awaiting_original_match"
            commit_with_sqlite_retry(db.session)
            notify_rfa_awaiting_match(slug, req, player=player)
            flash(
                f"Player accepted and allows matching (roll {match_roll:.1f}). Original team notified.",
                "ok",
            )
        else:
            req.status = "awaiting_equalization"
            commit_with_sqlite_retry(db.session)
            notify_rfa_awaiting_equalization(slug, req, player=player)
            flash(
                f"Player accepted but blocked matching (roll {match_roll:.1f}). Equalization required.",
                "ok",
            )
        return redirect(url_for("site_admin.admin_rfa_offers"))
    req.status = "awaiting_original_match"
    commit_with_sqlite_retry(db.session)
    notify_rfa_awaiting_match(slug, req, player=player)
    flash("Player accepted — original team GM notified for match/reject (no compensation).", "ok")
    return redirect(url_for("site_admin.admin_rfa_offers"))


@site_admin_bp.post("/rfa-offers/<int:rid>/complete")
@login_required
def admin_rfa_complete(rid: int):
    require_admin_role(ADMIN_ROLE_STATS, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    req = db.session.get(RfaOfferRequest, rid)
    if not req or req.league_slug != slug:
        abort(404)
    note = (request.form.get("admin_note") or "").strip()
    req.admin_note = note
    req.status = "completed"
    req.processed_by_user_id = int(current_user.id)
    req.processed_at = datetime.utcnow()
    commit_with_sqlite_retry(db.session)
    player = db.session.get(Player, int(req.player_id))
    notify_rfa_offer_outcome(slug, req, player=player, title="RFA offer completed", body=note or "Commissioner marked complete.")
    flash("Offer marked completed.", "ok")
    return redirect(url_for("site_admin.admin_rfa_offers"))


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
            commit_with_sqlite_retry(db.session)
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
        commit_with_sqlite_retry(db.session)
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
        commit_with_sqlite_retry(db.session)
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


def _parse_draft_born_before_date(raw: str) -> date | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


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
        commit_with_sqlite_retry(db.session)
        _purge_draft_soundbite_dir(slug, int(draft_id_raw))
        flash(f"Deleted draft “{deleted_name}”.", "ok")
        return redirect(url_for("site_admin.admin_draft_hub"))
    if request.method == "POST" and request.form.get("action") == "new":
        from app.services.draft_hub_eligibility import DRAFT_POOL_AGE_RULES, default_eligibility_for_league
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
            eligibility_pool_source=DRAFT_POOL_AGE_RULES,
        )
        db.session.add(row)
        db.session.flush()
        from app.services.draft_hub_order import generate_draft_order_from_prior_season

        created, gen_err, summary = generate_draft_order_from_prior_season(
            db.session,
            db.session,
            league_slug=slug,
            draft=row,
        )
        if gen_err:
            commit_with_sqlite_retry(db.session)
            flash(f"Draft created. {gen_err}", "err")
        else:
            db.session.add(
                AdminAuditLog(
                    admin_user_id=int(current_user.id),
                    league_slug=slug,
                    action="draft_hub_generate_order",
                    detail_json=json.dumps(
                        {
                            "draft_id": int(row.id),
                            "created": created,
                            "auto_on_create": True,
                            **summary,
                        }
                    ),
                )
            )
            commit_with_sqlite_retry(db.session)
            label = str(summary.get("standings_season_label") or "prior season")
            msg = f"Draft created with {created} slots from {label} standings (worst → best)."
            if int(summary.get("traded_count") or 0):
                msg += f" {int(summary['traded_count'])} traded pick(s) applied."
            flash(msg, "ok")
        return redirect(url_for("site_admin.admin_draft_hub_edit", draft_id=row.id))
    rows = list(
        db.session.scalars(select(LeagueDraft).where(LeagueDraft.league_slug == slug).order_by(LeagueDraft.id.desc())).all()
    )
    return render_template("admin_draft_hub.html", league_slug=slug, drafts=rows)


@site_admin_bp.route("/draft-eligible-settings", methods=["GET", "POST"])
@login_required
def admin_draft_eligible_settings():
    require_admin_role(ADMIN_ROLE_CONTENT, ADMIN_ROLE_LEAGUE)
    slug = _league_slug()
    from app.services.draft_eligible_settings import (
        DRAFT_ELIGIBLE_POOL_MODE_AGE_RULES,
        DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
        DraftEligiblePageConfig,
        default_draft_eligible_page_config,
        format_draft_eligible_summary,
        load_draft_eligible_page_config,
        save_draft_eligible_page_config,
    )
    from app.services.draft_hub_eligibility import draft_eligible_timeline_year_for_league
    from app.services.draft_hub_eligibility_cache import invalidate_eligible_pool_cache
    from app.services.seasons import get_current_season

    season = get_current_season()
    season_timeline_year = draft_eligible_timeline_year_for_league(
        slug,
        int(season.start_year) if season and season.start_year else None,
        int(season.end_year) if season and season.end_year else None,
        datetime.utcnow().year,
    )
    defaults = default_draft_eligible_page_config(slug, timeline_year=season_timeline_year)
    if request.method == "POST":
        timeline_year = int(request.form.get("timeline_year") or season_timeline_year)
        if timeline_year < 1900 or timeline_year > 2100:
            flash("Timeline year must be between 1900 and 2100.", "err")
            return redirect(url_for("site_admin.admin_draft_eligible_settings"))
        pool_mode = (request.form.get("pool_mode") or defaults.pool_mode).strip()
        if pool_mode not in {
            DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
            DRAFT_ELIGIBLE_POOL_MODE_AGE_RULES,
        }:
            pool_mode = defaults.pool_mode
        birth_start = _parse_draft_born_before_date(request.form.get("birth_start") or "")
        birth_end = _parse_draft_born_before_date(request.form.get("birth_end") or "")
        if birth_start is None:
            birth_start = defaults.birth_start
        if birth_end is None:
            birth_end = defaults.birth_end
        if birth_end < birth_start:
            flash("Birth end date must be on or after the birth start date.", "err")
            return redirect(url_for("site_admin.admin_draft_eligible_settings"))
        min_anchor_month, min_anchor_day = _parse_draft_deadline_date(
            request.form.get("min_deadline_date") or "",
            defaults.min_anchor_month,
            defaults.min_anchor_day,
        )
        max_anchor_month, max_anchor_day = _parse_draft_deadline_date(
            request.form.get("max_deadline_date") or "",
            defaults.max_anchor_month,
            defaults.max_anchor_day,
        )
        config = DraftEligiblePageConfig(
            timeline_year=timeline_year,
            pool_mode=pool_mode,
            birth_start=birth_start,
            birth_end=birth_end,
            exclude_eastern_bloc=request.form.get("exclude_eastern_bloc") == "1",
            min_age_years=int(request.form.get("min_age_years") or defaults.min_age_years),
            min_anchor_month=min_anchor_month,
            min_anchor_day=min_anchor_day,
            max_age_years=int(request.form.get("max_age_years") or defaults.max_age_years),
            max_anchor_month=max_anchor_month,
            max_anchor_day=max_anchor_day,
        )
        save_draft_eligible_page_config(
            db.session,
            slug,
            config,
            updated_by_user_id=int(current_user.id),
        )
        invalidate_eligible_pool_cache(league_slug=slug)
        db.session.add(
            AdminAuditLog(
                admin_user_id=int(current_user.id),
                league_slug=slug,
                action="draft_eligible_settings_update",
                detail_json=json.dumps(
                    {
                        "timeline_year": config.timeline_year,
                        "pool_mode": config.pool_mode,
                        "birth_start": config.birth_start.isoformat(),
                        "birth_end": config.birth_end.isoformat(),
                        "exclude_eastern_bloc": config.exclude_eastern_bloc,
                    }
                ),
            )
        )
        commit_with_sqlite_retry(db.session)
        flash("Draft Eligible settings saved.", "ok")
        return redirect(url_for("site_admin.admin_draft_eligible_settings"))

    config = load_draft_eligible_page_config(
        db.session,
        slug,
        season_timeline_year=season_timeline_year,
    )
    timeline_year = int(config.timeline_year)
    min_deadline_value = date(timeline_year, config.min_anchor_month, config.min_anchor_day).isoformat()
    max_deadline_value = date(timeline_year, config.max_anchor_month, config.max_anchor_day).isoformat()
    preview_summary = format_draft_eligible_summary(
        config,
        league_slug=slug,
    )
    age_options = list(range(15, 31))
    return render_template(
        "admin_draft_eligible_settings.html",
        league_slug=slug,
        config=config,
        defaults=defaults,
        timeline_year=timeline_year,
        season_timeline_year=season_timeline_year,
        birth_start_value=config.birth_start.isoformat(),
        birth_end_value=config.birth_end.isoformat(),
        min_deadline_value=min_deadline_value,
        max_deadline_value=max_deadline_value,
        preview_summary=preview_summary,
        age_options=age_options,
        pool_mode_birth_window=DRAFT_ELIGIBLE_POOL_MODE_BIRTH_WINDOW,
        pool_mode_age_rules=DRAFT_ELIGIBLE_POOL_MODE_AGE_RULES,
        public_draft_eligible_url=url_for("main.draft_eligible"),
    )


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
            from app.services.draft_hub_eligibility import (
                DRAFT_POOL_AGE_RULES,
                DRAFT_POOL_BORN_BEFORE,
                DRAFT_POOL_DRAFT_ELIGIBLE_PAGE,
                DRAFT_POOL_SOURCE_VALUES,
            )

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
            pool_source = (request.form.get("eligibility_pool_source") or DRAFT_POOL_AGE_RULES).strip()
            if pool_source not in DRAFT_POOL_SOURCE_VALUES:
                pool_source = DRAFT_POOL_AGE_RULES
            row.eligibility_pool_source = pool_source
            row.born_before_date = (
                _parse_draft_born_before_date(request.form.get("born_before_date") or "")
                if pool_source == DRAFT_POOL_BORN_BEFORE
                else None
            )
            if pool_source == DRAFT_POOL_BORN_BEFORE and row.born_before_date is None:
                row.eligibility_pool_source = DRAFT_POOL_AGE_RULES
                flash("Player born before date was invalid, so the draft stayed on age rules.", "err")
            elif pool_source == DRAFT_POOL_DRAFT_ELIGIBLE_PAGE:
                flash("Draft pool will use the public Draft Eligible page list.", "ok")
            row.scheduled_start_at = _parse_scheduled_start(request.form.get("scheduled_start_at") or "")
            row.gm_picks_enabled = request.form.get("gm_picks_enabled") == "1"
            row.discord_on_deck_enabled = request.form.get("discord_on_deck_enabled") == "1"
            commit_with_sqlite_retry(db.session)
            flash("Settings saved.", "ok")
        elif act == "save_controls" and row.status in ("setup", "live"):
            row.gm_picks_enabled = request.form.get("gm_picks_enabled") == "1"
            row.discord_on_deck_enabled = request.form.get("discord_on_deck_enabled") == "1"
            commit_with_sqlite_retry(db.session)
            flash("Draft controls updated.", "ok")
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
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
        elif act == "end_draft_early" and row.status == "live":
            from app.services.draft_hub_state import end_draft_early

            err = end_draft_early(db.session, row, int(current_user.id))
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_end_early",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Draft ended and marked complete.", "ok")
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
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
                commit_with_sqlite_retry(db.session)
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
        elif act == "generate_order_from_standings" and row.status == "setup":
            from app.services.draft_hub_order import generate_draft_order_from_prior_season

            old_tiers = {
                int(s.overall_pick): s.boost_tier or ""
                for s in db.session.scalars(
                    select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id)
                ).all()
            }
            old_penalties = {
                int(s.overall_pick)
                for s in db.session.scalars(
                    select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id)
                ).all()
                if bool(getattr(s, "penalty_pick", False))
            }
            db.session.execute(delete(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == row.id))
            created, err, summary = generate_draft_order_from_prior_season(
                db.session,
                db.session,
                league_slug=slug,
                draft=row,
                preserve_boost_tiers=old_tiers,
                preserve_penalty_picks=old_penalties,
            )
            if err:
                db.session.rollback()
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_generate_order",
                        detail_json=json.dumps(
                            {
                                "draft_id": row.id,
                                "created": created,
                                **summary,
                            }
                        ),
                    )
                )
                commit_with_sqlite_retry(db.session)
                label = str(summary.get("standings_season_label") or "prior season")
                msg = (
                    f"Generated {created} slots from {label} standings "
                    f"(worst record → pick 1)."
                )
                traded = int(summary.get("traded_count") or 0)
                if traded:
                    msg += f" {traded} pick(s) use admin-managed trade ownership."
                if not summary.get("has_ownership_rows"):
                    msg += (
                        " No active draft-pick ownership panels were found for this league — "
                        "owners match original teams until panels are configured and regenerated."
                    )
                auto_penalties = int(summary.get("auto_penalties_applied") or 0)
                if auto_penalties:
                    msg += f" Cap strike penalties auto-applied: {auto_penalties}."
                flash(msg, "ok")
                for warning in list(summary.get("strike_warnings") or []):
                    if warning:
                        flash(str(warning), "warn")
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
                penalty_pick = request.form.get(f"slot_penalty_{overall}") == "1"
                db.session.add(
                    LeagueDraftSlot(
                        league_draft_id=row.id,
                        overall_pick=overall,
                        round=round_no,
                        original_team_id=orig_tid,
                        team_id=tid,
                        boost_tier=old_tiers.get(overall, ""),
                        penalty_pick=penalty_pick,
                    )
                )
                created += 1
            commit_with_sqlite_retry(db.session)
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
            penalty_changed = 0
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
                new_penalty = request.form.get(f"slot_penalty_{overall}") == "1"
                if bool(getattr(slot, "penalty_pick", False)) != new_penalty:
                    slot.penalty_pick = new_penalty
                    penalty_changed += 1
            if changed:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="draft_hub_change_pick_teams",
                        detail_json=json.dumps({"draft_id": row.id, "changed": changed}),
                    )
                )
            commit_with_sqlite_retry(db.session)
            msg = f"Pick ownership saved ({changed} team change(s), {penalty_changed} penalty flag change(s))."
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
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
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
                    "penalty_pick": bool(getattr(slot, "penalty_pick", False)) if slot else False,
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
    born_before_value = ""
    if getattr(row, "born_before_date", None):
        born_before_value = row.born_before_date.isoformat()
    from app.services.draft_hub_order import resolve_prior_season_for_draft
    from app.services.draft_hub_eligibility import (
        DRAFT_POOL_AGE_RULES,
        DRAFT_POOL_BORN_BEFORE,
        DRAFT_POOL_DRAFT_ELIGIBLE_PAGE,
    )
    from app.services.seasons import season_display_label

    standings_season = resolve_prior_season_for_draft(db.session, draft_year=int(row.timeline_year))
    standings_order_label = (
        season_display_label(standings_season)
        if standings_season is not None
        else f"{int(row.timeline_year) - 1}–{int(row.timeline_year) % 100:02d}"
    )
    return render_template(
        "admin_draft_hub_edit.html",
        league_slug=slug,
        draft=row,
        teams=teams,
        standings_order_label=standings_order_label,
        slots_csv=slots_csv,
        round_slot_rows=round_slot_rows,
        wishlist_guidance=wishlist_guidance,
        gold_csv=gold_csv,
        silver_csv=silver_csv,
        sounds=sounds,
        sched_value=sched,
        min_deadline_value=min_deadline_value,
        max_deadline_value=max_deadline_value,
        born_before_value=born_before_value,
        year_min_date=year_min_date,
        year_max_date=year_max_date,
        draft_pool_age_rules=DRAFT_POOL_AGE_RULES,
        draft_pool_born_before=DRAFT_POOL_BORN_BEFORE,
        draft_pool_draft_eligible_page=DRAFT_POOL_DRAFT_ELIGIBLE_PAGE,
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
        commit_with_sqlite_retry(db.session)
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
        )
        db.session.add(row)
        commit_with_sqlite_retry(db.session)
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
        end_expansion_draft_early,
        exempt_team_ids,
        expansion_franchise_ids_sorted,
        EXPANSION_ORDER_FORMAT_VALUES,
        EXPANSION_ORDER_SERPENTINE,
        EXPANSION_ORDER_STRAIGHT,
        go_live,
        player_excluded_from_expansion_pool,
        regenerate_slots,
        replace_eligible_players,
        resolve_admin_pick,
        set_exempt_team_ids,
        set_expansion_team_order,
        skip_current_slot,
        undo_last_admin_skip,
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
            fmt_raw = (request.form.get("phase_order_format") or EXPANSION_ORDER_STRAIGHT).strip().lower()
            order_fmt = fmt_raw if fmt_raw in EXPANSION_ORDER_FORMAT_VALUES else EXPANSION_ORDER_STRAIGHT
            serp_cont = (
                request.form.get("serpentine_continuous") == "1"
                and order_fmt == EXPANSION_ORDER_SERPENTINE
            )
            if not err_msg and len(exp_list) > 1:
                if g_first is None or g_first not in exp_list:
                    err_msg = (
                        "Choose which expansion franchise picks first in the goalie phase "
                        "(must be one of the selected expansion clubs)."
                    )
                elif not serp_cont and (s_first is None or s_first not in exp_list):
                    err_msg = (
                        "Choose which expansion franchise picks first in the skater phase "
                        "(must be one of the selected expansion clubs)."
                    )
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
                row.phase_order_format = order_fmt
                row.serpentine_continuous = serp_cont
                row.scheduled_start_at = _parse_scheduled_start(request.form.get("scheduled_start_at") or "")
                set_expansion_team_order(row, exp_list)
                row.goalie_phase_first_team_id = g_first
                row.skater_phase_first_team_id = s_first if not serp_cont else g_first
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
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
        elif act == "save_eligible" and row.status in ("setup", "live"):
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
                    detail_json=json.dumps({"draft_id": row.id, "count": len(pids), "status": row.status}),
                )
            )
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
        elif act == "skip_pick" and row.status == "live":
            err = skip_current_slot(db.session, row, int(current_user.id))
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="expansion_draft_skip_pick",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Current pick skipped.", "ok")
            commit_with_sqlite_retry(db.session)
        elif act == "undo_skip" and row.status == "live":
            err = undo_last_admin_skip(db.session, row)
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="expansion_draft_undo_skip",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Last skip undone; that team is back on the clock.", "ok")
            commit_with_sqlite_retry(db.session)
        elif act == "end_draft_early" and row.status == "live":
            err = end_expansion_draft_early(db.session, row, int(current_user.id))
            if err:
                flash(err, "err")
            else:
                db.session.add(
                    AdminAuditLog(
                        admin_user_id=int(current_user.id),
                        league_slug=slug,
                        action="expansion_draft_end_early",
                        detail_json=json.dumps({"draft_id": row.id}),
                    )
                )
                flash("Expansion draft ended and marked complete.", "ok")
            commit_with_sqlite_retry(db.session)
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
            commit_with_sqlite_retry(db.session)
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
    from app.services.draft_hub_eligibility import age_as_of
    from app.services.free_agents import player_ids_from_player_rights_csv_for_team
    from app.services.seasons import get_current_season, season_age_reference_date

    age_ref = season_age_reference_date(get_current_season())
    max_birth_for_expansion_pool = date(age_ref.year - 21, age_ref.month, age_ref.day)

    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)))
    expansion_org_players: dict[int, dict[str, list[Player]]] = {}
    players_all = list(
        db.session.scalars(
            select(Player)
            .where(Player.retired.is_(False))
            .where(Player.birth_date.isnot(None))
            .where(Player.birth_date <= max_birth_for_expansion_pool)
            .options(joinedload(Player.contract), joinedload(Player.current_team))
            .order_by(Player.full_name.asc())
        ).unique().all()
    )
    player_ids = [int(p.id) for p in players_all]
    prospect_by_pid: dict[int, Prospect] = {}
    if player_ids:
        for pr in db.session.scalars(select(Prospect).where(Prospect.player_id.in_(player_ids))).all():
            if pr.player_id is None:
                continue
            pid = int(pr.player_id)
            if pid not in prospect_by_pid:
                prospect_by_pid[pid] = pr

    def _expansion_pool_age_ok(pl: Player) -> bool:
        ag = age_as_of(pl.birth_date, age_ref)
        return ag is not None and ag >= 21

    for pl in players_all:
        if not _expansion_pool_age_ok(pl):
            continue
        if player_excluded_from_expansion_pool(db.session, pl):
            continue
        pr = prospect_by_pid.get(int(pl.id))
        org = organization_main_team(db.session, pl, prospect=pr)
        if org is None:
            continue
        tid = int(org.id)
        if tid in exempt:
            continue
        if pl.contract is not None:
            ct = pl.current_team
            if ct is not None and is_main_league_team(ct) and int(ct.id) == tid:
                bucket = "main"
            else:
                bucket = "minors"
        else:
            bucket = "rights"
        expansion_org_players.setdefault(tid, {"main": [], "minors": [], "rights": []})[bucket].append(pl)

    def _player_ids_already_listed_for_team(team_id: int) -> set[int]:
        b = expansion_org_players.get(team_id) or {}
        out: set[int] = set()
        for key in ("main", "minors", "rights"):
            for p in b.get(key, []):
                out.add(int(p.id))
        return out

    players_by_id: dict[int, Player] = {int(p.id): p for p in players_all}
    for tm in main_teams:
        tid = int(tm.id)
        if tid in exempt:
            continue
        if not raw_dir.is_dir():
            continue
        csv_pids = player_ids_from_player_rights_csv_for_team(db.session, raw_dir, tm)
        if not csv_pids:
            continue
        seen = _player_ids_already_listed_for_team(tid)
        for pid in csv_pids:
            if pid in seen:
                continue
            pl = players_by_id.get(pid) or db.session.get(Player, pid)
            if pl is None or pl.retired:
                continue
            if not _expansion_pool_age_ok(pl):
                continue
            if player_excluded_from_expansion_pool(db.session, pl):
                continue
            expansion_org_players.setdefault(tid, {"main": [], "minors": [], "rights": []})
            expansion_org_players[tid]["rights"].append(pl)
            seen.add(pid)

    for _tid, buckets in expansion_org_players.items():
        buckets["main"].sort(key=lambda p: (p.full_name or "").lower())
        buckets["minors"].sort(key=lambda p: (p.full_name or "").lower())
        buckets["rights"].sort(key=lambda p: (p.full_name or "").lower())

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
