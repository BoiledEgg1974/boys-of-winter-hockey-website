"""Public Draft Hub page + JSON API + sound playback."""
from __future__ import annotations

from pathlib import Path

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from app.auth_login import active_membership_for_league
from app.league_db import db
from app.logo_urls import team_logo_url_for_team
from app.models import Player, Team
from app.services.draft_hub_ai_advisor import fetch_draft_hub_ai_advice
from app.services.draft_hub_eligibility import age_as_of, eligible_players_ordered
from app.services.seasons import get_current_season, season_age_reference_date
from app.services.draft_hub_state import (
    auto_complete_draft,
    compute_winners_losers,
    draft_eligibility_params,
    end_draft_early,
    featured_draft,
    gm_user_ids_for_team,
    pause_draft_timer,
    picked_player_ids,
    process_tick,
    record_pick,
    resolve_admin_pick,
    resume_draft_timer,
    slots_ordered,
    swap_draft_slot_team_ids,
    utcnow_naive,
    wishlist_head_for_user,
)
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.site_models import (
    LeagueDraft,
    LeagueDraftPick,
    LeagueDraftQueueItem,
    LeagueDraftSlot,
    LeagueDraftSoundbite,
)

draft_hub_bp = Blueprint("draft_hub", __name__, url_prefix="/draft-hub")


def _league_slug() -> str:
    return str(current_app.config.get("LEAGUE_SLUG") or "")


def _membership():
    if not current_user.is_authenticated:
        return None
    return active_membership_for_league(current_user, _league_slug())


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


@draft_hub_bp.get("")
def draft_hub_page():
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    team_by_id = {t.id: t for t in teams}
    return render_template(
        "draft_hub.html",
        featured_draft=draft,
        team_by_id=team_by_id,
        gm_membership=_membership(),
    )


@draft_hub_bp.get("/archive")
def draft_hub_archive_list():
    slug = _league_slug()
    rows = list(
        db.session.scalars(
            select(LeagueDraft)
            .where(LeagueDraft.league_slug == slug, LeagueDraft.status == "completed")
            .order_by(LeagueDraft.id.desc())
        ).all()
    )
    teams = {t.id: t for t in db.session.scalars(select(Team)).all()}
    return render_template("draft_hub_archive_list.html", drafts=rows, team_by_id=teams)


@draft_hub_bp.get("/archive/<int:draft_id>")
def draft_hub_archive_one(draft_id: int):
    slug = _league_slug()
    draft = db.session.get(LeagueDraft, draft_id)
    if not draft or draft.league_slug != slug or draft.status != "completed":
        abort(404)
    picks = list(
        db.session.scalars(
            select(LeagueDraftPick)
            .where(LeagueDraftPick.league_draft_id == draft.id)
            .order_by(LeagueDraftPick.overall_pick.asc())
        ).all()
    )
    pids = [p.player_id for p in picks]
    players = {}
    if pids:
        for pl in db.session.scalars(select(Player).where(Player.id.in_(pids))).unique().all():
            players[pl.id] = pl
    teams = {t.id: t for t in db.session.scalars(select(Team)).all()}
    team_logo_url_by_id: dict[int, str] = {
        int(tid): team_logo_url_for_team(tm) for tid, tm in teams.items()
    }
    summary = compute_winners_losers(db.session, draft)
    boost_tier_by_overall = {
        int(s.overall_pick): s.boost_tier
        for s in db.session.scalars(
            select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == draft.id)
        ).all()
        if s.boost_tier
    }
    slots = list(
        db.session.scalars(
            select(LeagueDraftSlot)
            .where(LeagueDraftSlot.league_draft_id == draft.id)
            .order_by(LeagueDraftSlot.overall_pick.asc())
        ).all()
    )
    slot_by_overall = {int(s.overall_pick): s for s in slots}
    pick_by_overall = {int(p.overall_pick): p for p in picks}
    max_round = max([int(s.round) for s in slots] + [int(p.round) for p in picks] + [int(draft.rounds or 1)])
    picks_per_round = max(1, int(getattr(draft, "picks_per_round", 27) or 27))
    archive_rounds = []
    for round_no in range(1, max_round + 1):
        rows = []
        for pick_index in range(1, picks_per_round + 1):
            overall = ((round_no - 1) * picks_per_round) + pick_index
            slot = slot_by_overall.get(overall)
            pick = pick_by_overall.get(overall)
            if not slot and not pick:
                continue
            current_team_id = int(pick.team_id) if pick else (int(slot.team_id) if slot else None)
            original_team_id = (
                int(slot.original_team_id or slot.team_id)
                if slot
                else current_team_id
            )
            rows.append(
                {
                    "overall": overall,
                    "original_team_id": original_team_id,
                    "current_team_id": current_team_id,
                    "player_id": int(pick.player_id) if pick else None,
                    "boost_tier": (slot.boost_tier if slot else "") or "",
                }
            )
        if rows:
            archive_rounds.append({"round": round_no, "rows": rows})
    archive_round_groups = [
        {"rounds": group, "max_rows": max((len(r["rows"]) for r in group), default=0)}
        for group in (archive_rounds[i : i + 5] for i in range(0, len(archive_rounds), 5))
    ]
    return render_template(
        "draft_hub_archive_one.html",
        draft=draft,
        picks=picks,
        players=players,
        team_by_id=teams,
        team_logo_url_by_id=team_logo_url_by_id,
        summary=summary,
        boost_tier_by_overall=boost_tier_by_overall,
        archive_round_groups=archive_round_groups,
    )


@draft_hub_bp.get("/api/state")
def draft_hub_api_state():
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft:
        return jsonify({"ok": True, "draft": None})

    if draft.status == "live":
        process_tick(db.session, draft)
        db.session.commit()
        db.session.refresh(draft)

    slots = slots_ordered(db.session, draft.id)
    pick_overalls = {
        int(x)
        for x in db.session.scalars(
            select(LeagueDraftPick.overall_pick).where(LeagueDraftPick.league_draft_id == draft.id)
        ).all()
    }
    team_by_id = {t.id: t for t in db.session.scalars(select(Team)).all()}
    logo_by_team_id: dict[int, str] = {
        int(tid): team_logo_url_for_team(tm) for tid, tm in team_by_id.items()
    }
    boost_tier_by_overall: dict[int, str] = {
        int(s.overall_pick): s.boost_tier for s in slots if s.boost_tier
    }
    # Map every slot's overall_pick → original team id (the team that started with the pick).
    # We populate from slot.original_team_id when set; otherwise fall back to the current owner,
    # so untraded picks report identical current/original and the UI quietly skips the badge.
    original_team_by_overall: dict[int, int] = {
        int(s.overall_pick): int(s.original_team_id or s.team_id)
        for s in slots
        if s.team_id is not None
    }

    def _team_color(tm: Team | None) -> str | None:
        raw = (getattr(tm, "primary_color", None) or "").strip()
        if not raw.startswith("#") or len(raw) not in (4, 7):
            return None
        if not all(ch in "0123456789abcdefABCDEF" for ch in raw[1:]):
            return None
        return raw

    def _orig_team_meta(
        overall_pick: int,
        current_team_id: int | None,
    ) -> tuple[int | None, str | None, str | None]:
        orig_tid = original_team_by_overall.get(int(overall_pick))
        if orig_tid is None or current_team_id is None or orig_tid == int(current_team_id):
            return None, None, None
        orig_tm = team_by_id.get(int(orig_tid))
        return int(orig_tid), (orig_tm.abbreviation if orig_tm else None), _team_color(orig_tm)

    def _pick_dict(pk: LeagueDraftPick) -> dict:
        pl = db.session.get(Player, pk.player_id)
        tm = team_by_id.get(pk.team_id)
        orig_tid, orig_abbr, orig_color = _orig_team_meta(
            int(pk.overall_pick),
            int(pk.team_id) if pk.team_id is not None else None,
        )
        return {
            "overall": pk.overall_pick,
            "round": pk.round,
            "team": tm.full_display_name() if tm else str(pk.team_id),
            "team_id": int(pk.team_id) if pk.team_id is not None else None,
            "team_logo_url": logo_by_team_id.get(int(pk.team_id)) if pk.team_id is not None else None,
            "player": pl.full_name if pl else str(pk.player_id),
            "player_id": pk.player_id,
            "source": pk.source,
            "boost_tier": boost_tier_by_overall.get(int(pk.overall_pick), ""),
            "original_team_id": orig_tid,
            "original_team_abbr": orig_abbr,
            "original_team_color": orig_color,
        }

    all_picks_asc = list(
        db.session.scalars(
            select(LeagueDraftPick)
            .where(LeagueDraftPick.league_draft_id == draft.id)
            .order_by(LeagueDraftPick.overall_pick.asc())
        ).all()
    )
    ticker_picks = [_pick_dict(pk) for pk in all_picks_asc]
    tail = all_picks_asc[-24:] if len(all_picks_asc) > 24 else all_picks_asc
    pick_payload = [_pick_dict(pk) for pk in reversed(tail)]

    current_slot = None
    on_clock_team = None
    on_clock_team_id = None
    on_clock_logo_url = None
    up_next: list[dict] = []
    order_rows = []
    if slots:
        for i, s in enumerate(slots):
            tm = team_by_id.get(s.team_id)
            orig_tid, orig_abbr, orig_color = _orig_team_meta(
                int(s.overall_pick),
                int(s.team_id) if s.team_id is not None else None,
            )
            order_rows.append(
                {
                    "overall": s.overall_pick,
                    "round": s.round,
                    "team_id": s.team_id,
                    "team": tm.full_display_name() if tm else str(s.team_id),
                    "team_logo_url": logo_by_team_id.get(int(s.team_id)) if s.team_id is not None else None,
                    "forfeited": s.forfeited,
                    "boost_tier": s.boost_tier or "",
                    "original_team_id": orig_tid,
                    "original_team_abbr": orig_abbr,
                    "original_team_color": orig_color,
                    "is_current": bool(
                        draft.status == "live"
                        and i == draft.current_slot_index
                        and not s.forfeited
                        and not draft.awaiting_admin_resolution
                    ),
                    "has_pick": int(s.overall_pick) in pick_overalls,
                }
            )
        if draft.status == "live" and draft.current_slot_index < len(slots):
            cs = slots[draft.current_slot_index]
            if not cs.forfeited:
                cs_tm = team_by_id.get(cs.team_id)
                current_slot = {
                    "overall": cs.overall_pick,
                    "round": cs.round,
                    "team_id": cs.team_id,
                    "team": cs_tm.full_display_name() if cs_tm else str(cs.team_id),
                    "team_logo_url": logo_by_team_id.get(int(cs.team_id)) if cs.team_id is not None else None,
                    "boost_tier": cs.boost_tier or "",
                }
                on_clock_team = cs_tm.full_display_name() if cs_tm else str(cs.team_id)
                on_clock_team_id = int(cs.team_id) if cs.team_id is not None else None
                on_clock_logo_url = logo_by_team_id.get(int(cs.team_id)) if cs.team_id is not None else None

        # Build Up Next preview (on deck + in the hole) — next two non-forfeited slots.
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
                        "team_id": int(ns.team_id) if ns.team_id is not None else None,
                        "team": ns_tm.full_display_name() if ns_tm else str(ns.team_id),
                        "team_logo_url": logo_by_team_id.get(int(ns.team_id)) if ns.team_id is not None else None,
                        "boost_tier": ns.boost_tier or "",
                    }
                )

    params = draft_eligibility_params(draft)
    picked = picked_player_ids(db.session, draft.id)
    eligible = eligible_players_ordered(db.session, slug, params)
    eligible = [p for p in eligible if p.id not in picked]
    eligible_count = len(eligible)

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

    sounds = [
        {"id": s.id, "name": s.display_name}
        for s in db.session.scalars(
            select(LeagueDraftSoundbite).where(LeagueDraftSoundbite.league_draft_id == draft.id)
        ).all()
    ]

    mem = _membership()
    queue_ids: list[int] = []
    queue_items: list[dict] = []
    if mem and draft.id and current_user.is_authenticated:
        qitems = list(
            db.session.scalars(
                select(LeagueDraftQueueItem)
                .where(
                    LeagueDraftQueueItem.league_draft_id == draft.id,
                    LeagueDraftQueueItem.user_id == current_user.id,
                )
                .order_by(LeagueDraftQueueItem.sort_order.asc(), LeagueDraftQueueItem.id.asc())
            ).all()
        )
        queue_ids = [int(x.player_id) for x in qitems]
        q_pids = [int(x.player_id) for x in qitems]
        name_by_pid: dict[int, str] = {}
        if q_pids:
            for pl in db.session.scalars(select(Player).where(Player.id.in_(q_pids))).unique().all():
                name_by_pid[int(pl.id)] = pl.full_name or ""
        queue_items = [
            {
                "id": int(x.id),
                "player_id": int(x.player_id),
                "name": name_by_pid.get(int(x.player_id), ""),
            }
            for x in qitems
        ]

    can_pick = bool(
        mem
        and draft.status == "live"
        and not draft.awaiting_admin_resolution
        and current_slot
        and mem.team_id == current_slot["team_id"]
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
    unpicked_tradeable = sum(
        1 for s in slots if not s.forfeited and int(s.overall_pick) not in pick_overalls
    )
    can_admin_slot_swap = bool(
        current_user.is_authenticated
        and getattr(current_user, "is_admin", False)
        and draft.status == "live"
        and unpicked_tradeable >= 2
    )
    can_admin_end_early = bool(
        current_user.is_authenticated
        and getattr(current_user, "is_admin", False)
        and draft.status == "live"
    )
    wishlist_pick: dict[str, object] | None = None
    if can_pick:
        wpid, wname = wishlist_head_for_user(db.session, draft, slug, int(current_user.id))
        if wpid is not None:
            wishlist_pick = {"player_id": int(wpid), "player_name": str(wname or f"Player #{wpid}")}

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
                "picks_per_round": int(getattr(draft, "picks_per_round", 27) or 27),
                "eligible_count": eligible_count,
                "order": order_rows,
                "recent_picks": pick_payload,
                "ticker_picks": ticker_picks,
                "sounds": sounds,
                "queue_player_ids": queue_ids,
                "queue_items": queue_items,
                "can_pick": can_pick,
                "can_admin_pick": can_admin_pick,
                "can_admin_control": can_admin_control,
                "can_admin_slot_swap": can_admin_slot_swap,
                "can_admin_end_early": can_admin_end_early,
                "wishlist_pick": wishlist_pick,
            },
        }
    )


@draft_hub_bp.get("/api/ai-advice")
def draft_hub_api_ai_advice():
    """JSON for the Draft Hub AI panel (entertainment only)."""
    slug = _league_slug()
    q_draft_id = request.args.get("draft_id", type=int)
    featured = featured_draft(db.session, slug)
    draft = featured
    if q_draft_id:
        row = db.session.get(LeagueDraft, q_draft_id)
        if row and row.league_slug == slug:
            draft = row
    if not draft or draft.league_slug != slug:
        return jsonify({"ok": False, "error": "no_draft"}), 404

    if draft.status != "live" or draft.awaiting_admin_resolution:
        return jsonify(
            {
                "ok": True,
                "active": False,
                "headline": None,
                "summary": None,
                "recommendations": [],
            }
        )

    process_tick(db.session, draft)
    db.session.commit()
    db.session.refresh(draft)

    slots = slots_ordered(db.session, draft.id)
    team_by_id = {t.id: t for t in db.session.scalars(select(Team)).all()}
    current_slot = None
    if (
        draft.status == "live"
        and draft.current_slot_index < len(slots)
        and not draft.awaiting_admin_resolution
    ):
        cs = slots[draft.current_slot_index]
        if not cs.forfeited:
            current_slot = cs

    if not current_slot:
        return jsonify(
            {
                "ok": True,
                "active": False,
                "headline": None,
                "summary": None,
                "recommendations": [],
            }
        )

    tm = team_by_id.get(current_slot.team_id)
    team_name = tm.full_display_name() if tm else str(current_slot.team_id)
    payload = fetch_draft_hub_ai_advice(
        db.session,
        slug,
        draft,
        team_id=int(current_slot.team_id),
        team_name=team_name,
        round_no=int(current_slot.round),
        overall=int(current_slot.overall_pick),
    )
    if payload.get("error"):
        return jsonify({
            "ok": False,
            "active": True,
            "error": payload["error"],
            "details": payload.get("details") or "",
        }), 503
    return jsonify({"ok": True, "active": True, **payload})


_VALID_POS_FILTERS: frozenset[str] = frozenset(
    {"LW", "C", "RW", "LD", "RD", "G", "F", "D"}
)
_FORWARD_TOKENS: frozenset[str] = frozenset({"LW", "C", "RW"})
_DEFENSE_TOKENS: frozenset[str] = frozenset({"LD", "RD"})


def _pos_tokens(label: str) -> set[str]:
    if not label:
        return set()
    return {tok.strip().upper() for tok in label.replace(",", "•").split("•") if tok.strip()}


def _player_matches_pos_filter(label: str, pos_filter: str) -> bool:
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


@draft_hub_bp.get("/api/eligible-page")
def draft_hub_eligible_page():
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft:
        return jsonify({"ok": True, "players": []})
    q = (request.args.get("q") or "").strip().lower()
    pos_filter = (request.args.get("pos") or "").strip().upper()
    if pos_filter and pos_filter not in _VALID_POS_FILTERS:
        pos_filter = ""
    offset = max(0, request.args.get("offset", type=int) or 0)
    limit = min(80, max(1, request.args.get("limit", type=int) or 40))
    params = draft_eligibility_params(draft)
    picked = picked_player_ids(db.session, draft.id)
    eligible = eligible_players_ordered(db.session, slug, params)
    eligible = [p for p in eligible if p.id not in picked]
    if q:
        eligible = [p for p in eligible if q in (p.full_name or "").lower()]
    pos_labels: dict[int, str] = {}
    if pos_filter:
        filtered: list = []
        for pl in eligible:
            label = player_positions_display_label(pl)
            pos_labels[int(pl.id)] = label
            if _player_matches_pos_filter(label, pos_filter):
                filtered.append(pl)
        eligible = filtered
    slice_ = eligible[offset : offset + limit]
    as_of = season_age_reference_date(get_current_season())

    def age_years(bd):
        return age_as_of(bd, as_of)

    out = []
    for pl in slice_:
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
    return jsonify({"ok": True, "players": out, "total": len(eligible), "offset": offset, "limit": limit})


@draft_hub_bp.post("/pick")
@login_required
def draft_hub_pick():
    from flask_wtf.csrf import validate_csrf

    slug = _league_slug()
    validate_csrf(request.form.get("csrf_token"))
    draft = featured_draft(db.session, slug)
    flash_err: str | None = None
    if not draft or draft.status != "live":
        flash_err = "No live draft."
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
    return redirect(url_for("draft_hub.draft_hub_page"))


@draft_hub_bp.post("/pause-timer")
@login_required
def draft_hub_pause_timer():
    from flask_wtf.csrf import validate_csrf

    validate_csrf(request.form.get("csrf_token"))
    if not getattr(current_user, "is_admin", False):
        abort(403)
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft:
        flash("No draft is configured.", "err")
    else:
        err = pause_draft_timer(db.session, draft)
        if err:
            flash(err, "err")
        else:
            flash("Draft countdown paused.", "ok")
    db.session.commit()
    return redirect(url_for("draft_hub.draft_hub_page"))


@draft_hub_bp.post("/resume-timer")
@login_required
def draft_hub_resume_timer():
    from flask_wtf.csrf import validate_csrf

    validate_csrf(request.form.get("csrf_token"))
    if not getattr(current_user, "is_admin", False):
        abort(403)
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft:
        flash("No draft is configured.", "err")
    else:
        err = resume_draft_timer(db.session, draft)
        if err:
            flash(err, "err")
        else:
            flash("Draft countdown resumed.", "ok")
    db.session.commit()
    return redirect(url_for("draft_hub.draft_hub_page"))


@draft_hub_bp.post("/auto-complete")
@login_required
def draft_hub_auto_complete():
    from flask_wtf.csrf import validate_csrf

    validate_csrf(request.form.get("csrf_token"))
    if not getattr(current_user, "is_admin", False):
        abort(403)
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft:
        flash("No draft is configured.", "err")
        return redirect(url_for("draft_hub.draft_hub_page"))
    if draft.status != "live":
        flash("Draft is not live.", "err")
        return redirect(url_for("draft_hub.draft_hub_page"))
    picks_made, err = auto_complete_draft(db.session, draft, int(current_user.id))
    if err:
        db.session.rollback()
        flash(f"Auto-complete stopped after {picks_made} pick(s): {err}", "err")
    else:
        db.session.commit()
        msg = f"Auto-complete made {picks_made} pick(s)."
        if draft.status == "completed":
            msg += " Draft is complete."
        flash(msg, "ok")
    return redirect(url_for("draft_hub.draft_hub_page"))


@draft_hub_bp.post("/queue/add")
@login_required
def draft_hub_queue_add():
    from flask_wtf.csrf import validate_csrf

    slug = _league_slug()
    validate_csrf(request.form.get("csrf_token"))
    draft = featured_draft(db.session, slug)
    pid_raw = (request.form.get("player_id") or "").strip()
    if not draft or not pid_raw.isdigit():
        from flask import flash

        flash("Invalid request.", "err")
        return redirect(url_for("draft_hub.draft_hub_page"))
    pid = int(pid_raw)
    params = draft_eligibility_params(draft)
    picked = picked_player_ids(db.session, draft.id)
    eligible_ids = {p.id for p in eligible_players_ordered(db.session, slug, params)} - picked
    if pid not in eligible_ids:
        from flask import flash

        flash("Player not eligible.", "err")
        return redirect(url_for("draft_hub.draft_hub_page"))
    exists = db.session.scalar(
        select(LeagueDraftQueueItem).where(
            LeagueDraftQueueItem.league_draft_id == draft.id,
            LeagueDraftQueueItem.user_id == current_user.id,
            LeagueDraftQueueItem.player_id == pid,
        )
    )
    if not exists:
        max_sort = db.session.scalar(
            select(func.max(LeagueDraftQueueItem.sort_order)).where(LeagueDraftQueueItem.league_draft_id == draft.id)
        )
        nxt = int(max_sort or 0) + 1
        db.session.add(
            LeagueDraftQueueItem(league_draft_id=draft.id, user_id=int(current_user.id), player_id=pid, sort_order=nxt)
        )
    db.session.commit()
    return redirect(url_for("draft_hub.draft_hub_page"))


@draft_hub_bp.post("/queue/remove")
@login_required
def draft_hub_queue_remove():
    from flask_wtf.csrf import validate_csrf

    validate_csrf(request.form.get("csrf_token"))
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    qid = (request.form.get("queue_id") or "").strip()
    if draft and qid.isdigit():
        row = db.session.get(LeagueDraftQueueItem, int(qid))
        if row and row.league_draft_id == draft.id and row.user_id == current_user.id:
            db.session.delete(row)
    db.session.commit()
    return redirect(url_for("draft_hub.draft_hub_page"))


@draft_hub_bp.post("/end-draft-early")
@login_required
def draft_hub_end_draft_early():
    from flask_wtf.csrf import validate_csrf

    validate_csrf(request.form.get("csrf_token"))
    if not getattr(current_user, "is_admin", False):
        abort(403)
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft:
        flash("No draft is configured.", "err")
    elif draft.status != "live":
        flash("Draft is not live.", "err")
    else:
        err = end_draft_early(db.session, draft, int(current_user.id))
        if err:
            flash(err, "err")
        else:
            flash("Draft ended and marked complete.", "ok")
    db.session.commit()
    return redirect(url_for("draft_hub.draft_hub_page"))


@draft_hub_bp.post("/admin/swap-slots")
@login_required
def draft_hub_admin_swap_slots():
    """JSON: swap ``team_id`` on two unpicked draft slots (commissioner trade)."""
    from flask_wtf.csrf import validate_csrf

    if not getattr(current_user, "is_admin", False):
        return jsonify({"ok": False, "error": "Forbidden."}), 403
    data = request.get_json(silent=True) or {}
    try:
        validate_csrf(data.get("csrf_token"))
    except Exception:  # noqa: BLE001
        return jsonify({"ok": False, "error": "Invalid CSRF token."}), 400
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft or draft.status != "live":
        return jsonify({"ok": False, "error": "No live draft."}), 400
    oa = data.get("overall_a")
    ob = data.get("overall_b")
    if oa is None or ob is None:
        return jsonify({"ok": False, "error": "Missing overall_a / overall_b."}), 400
    try:
        overall_a = int(oa)
        overall_b = int(ob)
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Overall picks must be integers."}), 400
    err = swap_draft_slot_team_ids(db.session, draft, overall_a, overall_b, int(current_user.id))
    if err:
        db.session.rollback()
        return jsonify({"ok": False, "error": err}), 400
    db.session.commit()
    db.session.refresh(draft)
    return jsonify({"ok": True, "error": None})


@draft_hub_bp.get("/sound/<int:sound_id>")
def draft_hub_sound(sound_id: int):
    from pathlib import Path

    row = db.session.get(LeagueDraftSoundbite, sound_id)
    if not row:
        abort(404)
    draft = db.session.get(LeagueDraft, row.league_draft_id)
    if not draft or draft.league_slug != _league_slug():
        abort(404)
    if draft.status not in ("live", "setup", "completed"):
        abort(404)
    base = Path(current_app.instance_path) / "draft_soundbites" / draft.league_slug / str(draft.id)
    path = base / row.stored_filename
    if not path.is_file():
        abort(404)
    return send_file(path, mimetype=row.mime_type or "audio/mpeg", as_attachment=False)
