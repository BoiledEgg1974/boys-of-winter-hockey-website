"""Expansion Draft Hub — admin + GM only."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.auth_login import active_membership_for_league
from app.league_db import db
from app.logo_urls import team_logo_url_for_team
from app.models import Player, Team
from app.services.draft_hub_eligibility import age_as_of
from app.services.seasons import get_current_season, season_age_reference_date
from app.services.expansion_draft_state import (
    eligible_player_ids_for_board,
    expansion_franchise_ids_sorted,
    expansion_process_tick,
    featured_expansion_draft,
    gm_user_ids_for_team,
    hydrate_players_for_ordered_ids,
    pause_timer,
    player_is_defense,
    player_is_forward,
    player_is_goalie,
    record_pick,
    resolve_admin_pick,
    resume_timer,
    slots_ordered,
    utcnow_naive,
)
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.site_models import LeagueExpansionDraftPick

expansion_draft_hub_bp = Blueprint("expansion_draft_hub", __name__, url_prefix="/expansion-draft-hub")

_VALID_POS_FILTERS: frozenset[str] = frozenset({"LW", "C", "RW", "LD", "RD", "G", "F", "D"})
_FORWARD_TOKENS: frozenset[str] = frozenset({"LW", "C", "RW"})
_DEFENSE_TOKENS: frozenset[str] = frozenset({"LD", "RD"})


def _pos_tokens(label: str) -> set[str]:
    if not label:
        return set()
    return {tok.strip().upper() for tok in label.replace(",", "•").split("•") if tok.strip()}


def _player_matches_pos_filter_local(label: str, pos_filter: str) -> bool:
    if pos_filter not in _VALID_POS_FILTERS:
        return True
    tokens = _pos_tokens(label)
    if not tokens:
        return False
    if pos_filter == "F":
        return bool(tokens & _FORWARD_TOKENS)
    if pos_filter == "D":
        return bool(tokens & _DEFENSE_TOKENS)
    return pos_filter in tokens


def _league_slug() -> str:
    return str(current_app.config.get("LEAGUE_SLUG") or "")


def _membership():
    if not current_user.is_authenticated:
        return None
    return active_membership_for_league(current_user, _league_slug())


def _expansion_hub_allowed() -> bool:
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, "is_admin", False):
        return True
    return _membership() is not None


def _player_photo_url(player: Player | None) -> str:
    if not player:
        return ""
    static_root = Path(current_app.root_path) / (current_app.static_folder or "static")
    rel = resolve_player_headshot_static_filename(
        static_root,
        player,
        str(current_app.config.get("PLAYER_HEADSHOTS_REL_DIR") or "players"),
    )
    return url_for("static", filename=rel) if rel else ""


@expansion_draft_hub_bp.get("")
def expansion_draft_hub_page():
    if not _expansion_hub_allowed():
        flash("Expansion Draft Hub is only available to league GMs and admins.", "err")
        return redirect(url_for("main.home"))
    slug = _league_slug()
    draft = featured_expansion_draft(db.session, slug)
    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    team_by_id = {t.id: t for t in teams}
    return render_template(
        "expansion_draft_hub.html",
        featured_draft=draft,
        team_by_id=team_by_id,
        gm_membership=_membership(),
    )


@expansion_draft_hub_bp.get("/api/state")
def expansion_draft_api_state():
    if not _expansion_hub_allowed():
        abort(403)
    slug = _league_slug()
    draft = featured_expansion_draft(db.session, slug)
    if not draft:
        return jsonify({"ok": True, "draft": None})

    if draft.status == "live":
        expansion_process_tick(db.session, draft)
        db.session.commit()
        db.session.refresh(draft)

    slots = slots_ordered(db.session, draft.id)
    team_by_id = {t.id: t for t in db.session.scalars(select(Team)).all()}
    logo_by_team_id: dict[int, str] = {
        int(tid): team_logo_url_for_team(tm) for tid, tm in team_by_id.items()
    }

    def _roster_slot(pl: Player | None, phase: str) -> str:
        """Single roster column for expansion tracker: G, LD, RD, LW, C, RW."""
        ph = (phase or "").strip().lower()
        if ph == "goalie":
            return "G"
        if not pl:
            return "LW"
        if player_is_goalie(pl):
            return "G"
        label = (player_positions_display_label(pl) or "").strip()
        tokens = _pos_tokens(label)
        if tokens & {"LD", "RD"}:
            if "LD" in tokens:
                return "LD"
            if "RD" in tokens:
                return "RD"
            return "LD"
        if tokens & {"LW", "C", "RW"}:
            for pref in ("LW", "C", "RW"):
                if pref in tokens:
                    return pref
        if "G" in tokens:
            return "G"
        if player_is_defense(pl):
            return "LD"
        if player_is_forward(pl):
            return "LW"
        return "LW"

    def _pick_dict(pk: LeagueExpansionDraftPick, player_by_id: dict[int, Player]) -> dict:
        pl = player_by_id.get(int(pk.player_id))
        tm = team_by_id.get(pk.team_id)
        from_tm = team_by_id.get(pk.from_team_id) if pk.from_team_id else None
        from_tid = int(pk.from_team_id) if pk.from_team_id is not None else None
        pos_disp = player_positions_display_label(pl) if pl else ""
        slot = _roster_slot(pl, str(pk.phase or ""))
        return {
            "overall": pk.overall_pick,
            "round": pk.round,
            "phase": pk.phase,
            "team": tm.full_display_name() if tm else str(pk.team_id),
            "team_id": int(pk.team_id) if pk.team_id is not None else None,
            "team_logo_url": logo_by_team_id.get(int(pk.team_id)) if pk.team_id is not None else None,
            "player": pl.full_name if pl else str(pk.player_id),
            "player_id": pk.player_id,
            "pos_display": pos_disp,
            "roster_slot": slot,
            "source": pk.source,
            "from_team": from_tm.full_display_name() if from_tm else "",
            "from_team_id": from_tid,
            "from_team_logo_url": logo_by_team_id.get(from_tid) if from_tid is not None else None,
            "boost_tier": "",
            "original_team_id": None,
            "original_team_abbr": None,
            "original_team_color": None,
        }

    all_picks_asc = list(
        db.session.scalars(
            select(LeagueExpansionDraftPick)
            .where(LeagueExpansionDraftPick.league_expansion_draft_id == draft.id)
            .order_by(LeagueExpansionDraftPick.overall_pick.asc())
        ).all()
    )
    pick_player_ids = list({int(pk.player_id) for pk in all_picks_asc})
    players_for_picks: dict[int, Player] = {}
    if pick_player_ids:
        for row in db.session.scalars(select(Player).where(Player.id.in_(pick_player_ids))).unique().all():
            players_for_picks[int(row.id)] = row
    ticker_picks = [_pick_dict(pk, players_for_picks) for pk in all_picks_asc]
    tail = all_picks_asc[-24:] if len(all_picks_asc) > 24 else all_picks_asc
    pick_payload = [_pick_dict(pk, players_for_picks) for pk in reversed(tail)]

    current_slot = None
    on_clock_team = None
    on_clock_team_id = None
    on_clock_logo_url = None
    up_next: list[dict] = []
    order_rows = []
    exp_order = expansion_franchise_ids_sorted(draft)
    picks_per_round_layout = max(1, len(exp_order) or 1)

    if slots:
        for i, s in enumerate(slots):
            tm = team_by_id.get(s.team_id)
            order_rows.append(
                {
                    "overall": s.overall_pick,
                    "round": s.round,
                    "phase": s.phase,
                    "team_id": s.team_id,
                    "team": tm.full_display_name() if tm else str(s.team_id),
                    "team_logo_url": logo_by_team_id.get(int(s.team_id)) if s.team_id is not None else None,
                    "forfeited": s.forfeited,
                    "boost_tier": "",
                    "original_team_id": None,
                    "original_team_abbr": None,
                    "original_team_color": None,
                    "is_current": bool(
                        draft.status == "live"
                        and i == draft.current_slot_index
                        and not s.forfeited
                        and not draft.awaiting_admin_resolution
                    ),
                }
            )
        if draft.status == "live" and draft.current_slot_index < len(slots):
            cs = slots[draft.current_slot_index]
            if not cs.forfeited:
                cs_tm = team_by_id.get(cs.team_id)
                current_slot = {
                    "overall": cs.overall_pick,
                    "round": cs.round,
                    "phase": cs.phase,
                    "team_id": cs.team_id,
                    "team": cs_tm.full_display_name() if cs_tm else str(cs.team_id),
                    "team_logo_url": logo_by_team_id.get(int(cs.team_id)) if cs.team_id is not None else None,
                    "boost_tier": "",
                }
                on_clock_team = cs_tm.full_display_name() if cs_tm else str(cs.team_id)
                on_clock_team_id = int(cs.team_id) if cs.team_id is not None else None
                on_clock_logo_url = logo_by_team_id.get(int(cs.team_id)) if cs.team_id is not None else None

        if draft.status == "live" and not draft.awaiting_admin_resolution:
            labels = ["On Deck", "In The Hole"]
            j = draft.current_slot_index + 1
            while j < len(slots) and len(up_next) < 2:
                ns = slots[j]
                j += 1
                if ns.forfeited:
                    continue
                ns_tm = team_by_id.get(ns.team_id)
                up_next.append(
                    {
                        "label": labels[len(up_next)],
                        "overall": ns.overall_pick,
                        "round": ns.round,
                        "phase": ns.phase,
                        "team_id": int(ns.team_id) if ns.team_id is not None else None,
                        "team": ns_tm.full_display_name() if ns_tm else str(ns.team_id),
                        "team_logo_url": logo_by_team_id.get(int(ns.team_id)) if ns.team_id is not None else None,
                        "boost_tier": "",
                    }
                )

    phase_filter = None
    exp_team_for_eligible = None
    if current_slot:
        phase_filter = str(current_slot.get("phase") or "")
        exp_team_for_eligible = int(current_slot["team_id"]) if current_slot.get("team_id") is not None else None

    eligible_count = len(
        eligible_player_ids_for_board(
            db.session,
            draft,
            phase=phase_filter if draft.status == "live" else None,
            expansion_team_id=exp_team_for_eligible if draft.status == "live" else None,
        )
    )

    now = utcnow_naive()
    deadline_ms = None
    if (
        draft.pick_deadline_at
        and draft.status == "live"
        and not draft.awaiting_admin_resolution
        and not getattr(draft, "timer_paused", False)
    ):
        ddl = draft.pick_deadline_at
        sec = (ddl - now).total_seconds()
        deadline_ms = max(0, int(sec * 1000)) if sec > 0 else 0

    mem = _membership()
    exp_set = set(exp_order)
    can_pick = bool(
        mem
        and draft.status == "live"
        and not draft.awaiting_admin_resolution
        and current_slot
        and mem.team_id == current_slot["team_id"]
        and int(mem.team_id) in exp_set
        and current_user.is_authenticated
        and int(current_user.id) in gm_user_ids_for_team(db.session, slug, current_slot["team_id"])
    )
    can_admin_pick = bool(
        current_user.is_authenticated
        and getattr(current_user, "is_admin", False)
        and draft.status == "live"
        and current_slot
    )
    can_admin_control = bool(
        current_user.is_authenticated
        and getattr(current_user, "is_admin", False)
        and draft.status == "live"
        and current_slot
    )

    rt_tid: int | None = None
    if draft.status == "live" and current_slot and current_slot.get("team_id") is not None:
        rt_tid = int(current_slot["team_id"])
    elif exp_order:
        rt_tid = int(exp_order[0])
    rt_tm = team_by_id.get(rt_tid) if rt_tid is not None else None
    roster_tracker = {
        "team_id": rt_tid,
        "team_name": rt_tm.full_display_name() if rt_tm else "",
        "team_logo_url": logo_by_team_id.get(int(rt_tid)) if rt_tid is not None else None,
        "subtitle": (
            "Slots for the expansion club currently on the clock."
            if draft.status == "live" and current_slot
            else "Slots for the first expansion club in rotation (on-clock club when the draft is live)."
        ),
        "expansion_franchise_ids": [int(x) for x in exp_order],
    }

    return jsonify(
        {
            "ok": True,
            "draft": {
                "id": draft.id,
                "name": draft.name,
                "status": draft.status,
                "scheduled_start_at": draft.scheduled_start_at.isoformat() if draft.scheduled_start_at else None,
                "awaiting_admin": bool(draft.awaiting_admin_resolution),
                "timer_paused": bool(getattr(draft, "timer_paused", False)),
                "timer_paused_remaining_seconds": draft.timer_paused_remaining_seconds,
                "on_clock_team": on_clock_team,
                "on_clock_team_id": on_clock_team_id,
                "on_clock_logo_url": on_clock_logo_url,
                "up_next": up_next,
                "current_slot": current_slot,
                "deadline_ms": deadline_ms,
                "timer_seconds": draft.timer_seconds,
                "picks_per_round": picks_per_round_layout,
                "eligible_count": eligible_count,
                "order": order_rows,
                "recent_picks": pick_payload,
                "ticker_picks": ticker_picks,
                "roster_tracker": roster_tracker,
                "sounds": [],
                "queue_player_ids": [],
                "queue_items": [],
                "can_pick": can_pick,
                "can_admin_pick": can_admin_pick,
                "can_admin_control": can_admin_control,
                "wishlist_pick": None,
            },
        }
    )


@expansion_draft_hub_bp.get("/api/eligible-page")
def expansion_draft_eligible_page():
    if not _expansion_hub_allowed():
        abort(403)
    slug = _league_slug()
    draft = featured_expansion_draft(db.session, slug)
    if not draft:
        return jsonify({"ok": True, "players": []})
    q = (request.args.get("q") or "").strip().lower()
    pos_filter = (request.args.get("pos") or "").strip().upper()
    if pos_filter and pos_filter not in _VALID_POS_FILTERS:
        pos_filter = ""
    offset = max(0, request.args.get("offset", type=int) or 0)
    limit = min(80, max(1, request.args.get("limit", type=int) or 40))

    phase_filter = None
    exp_team_id = None
    if draft.status == "live":
        slots = slots_ordered(db.session, draft.id)
        if draft.current_slot_index < len(slots):
            cs = slots[draft.current_slot_index]
            if not cs.forfeited:
                phase_filter = str(cs.phase or "")
                exp_team_id = int(cs.team_id)

    ordered_ids = eligible_player_ids_for_board(
        db.session,
        draft,
        phase=phase_filter,
        expansion_team_id=exp_team_id,
    )
    total = len(ordered_ids)

    if q or pos_filter:
        eligible = hydrate_players_for_ordered_ids(db.session, ordered_ids)
        if q:
            eligible = [p for p in eligible if q in (p.full_name or "").lower()]
        pos_labels: dict[int, str] = {}
        if pos_filter:
            filtered: list[Player] = []
            for pl in eligible:
                label = player_positions_display_label(pl)
                pos_labels[int(pl.id)] = label
                if _player_matches_pos_filter_local(label, pos_filter):
                    filtered.append(pl)
            eligible = filtered
        total = len(eligible)
        slice_players = eligible[offset : offset + limit]
    else:
        total = len(ordered_ids)
        slice_ids = ordered_ids[offset : offset + limit]
        slice_players = hydrate_players_for_ordered_ids(db.session, slice_ids)
        pos_labels = {}

    as_of = season_age_reference_date(get_current_season())

    def age_years(bd):
        return age_as_of(bd, as_of)

    out = []
    for pl in slice_players:
        rr = get_player_ratings_row(pl.fhm_player_id)
        label = pos_labels.get(int(pl.id)) or player_positions_display_label(pl)
        out.append(
            {
                "id": pl.id,
                "name": pl.full_name,
                "team": pl.current_team.full_display_name() if pl.current_team else "",
                "pos": label,
                "age": age_years(pl.birth_date),
                "pot": pl.overall_potential,
                "abi": pl.overall_ability,
                "w": rr.get("w") if rr else None,
                "l": rr.get("l") if rr else None,
                "gaa": rr.get("gaa") if rr else None,
                "svp": rr.get("svp") if rr else None,
                "height_in": pl.height_inches,
                "weight_lb": pl.weight_lbs,
                "photo_url": _player_photo_url(pl),
            }
        )
    return jsonify({"ok": True, "players": out, "total": total, "offset": offset, "limit": limit})
@login_required
def expansion_draft_pick():
    from flask_wtf.csrf import validate_csrf

    if not _expansion_hub_allowed():
        abort(403)
    slug = _league_slug()
    validate_csrf(request.form.get("csrf_token"))
    draft = featured_expansion_draft(db.session, slug)
    flash_err: str | None = None
    if not draft or draft.status != "live":
        flash_err = "No live expansion draft."
    else:
        pid_raw = (request.form.get("player_id") or "").strip()
        if not pid_raw.isdigit():
            flash_err = "Invalid player."
        elif getattr(current_user, "is_admin", False):
            flash_err = resolve_admin_pick(db.session, draft, int(pid_raw), int(current_user.id))
        else:
            flash_err = record_pick(db.session, draft, int(pid_raw), int(current_user.id), "gm")
    if flash_err:
        flash(flash_err, "err")
    db.session.commit()
    return redirect(url_for("expansion_draft_hub.expansion_draft_hub_page"))


@expansion_draft_hub_bp.post("/pause-timer")
@login_required
def expansion_draft_pause_timer():
    from flask_wtf.csrf import validate_csrf

    if not _expansion_hub_allowed():
        abort(403)
    validate_csrf(request.form.get("csrf_token"))
    if not getattr(current_user, "is_admin", False):
        abort(403)
    slug = _league_slug()
    draft = featured_expansion_draft(db.session, slug)
    if not draft:
        flash("No expansion draft is configured.", "err")
    else:
        err = pause_timer(db.session, draft)
        if err:
            flash(err, "err")
        else:
            flash("Countdown paused.", "ok")
    db.session.commit()
    return redirect(url_for("expansion_draft_hub.expansion_draft_hub_page"))


@expansion_draft_hub_bp.post("/resume-timer")
@login_required
def expansion_draft_resume_timer():
    from flask_wtf.csrf import validate_csrf

    if not _expansion_hub_allowed():
        abort(403)
    validate_csrf(request.form.get("csrf_token"))
    if not getattr(current_user, "is_admin", False):
        abort(403)
    slug = _league_slug()
    draft = featured_expansion_draft(db.session, slug)
    if not draft:
        flash("No expansion draft is configured.", "err")
    else:
        err = resume_timer(db.session, draft)
        if err:
            flash(err, "err")
        else:
            flash("Countdown resumed.", "ok")
    db.session.commit()
    return redirect(url_for("expansion_draft_hub.expansion_draft_hub_page"))
