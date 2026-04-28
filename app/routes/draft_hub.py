"""Public Draft Hub page + JSON API + sound playback."""
from __future__ import annotations

import json

from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from app.auth_login import active_membership_for_league
from app.league_db import db
from app.models import Player, Team
from app.services.draft_hub_eligibility import age_as_of, anchor_dates, eligible_players_ordered
from app.services.draft_hub_state import (
    draft_eligibility_params,
    featured_draft,
    gm_user_ids_for_team,
    picked_player_ids,
    process_tick,
    record_pick,
    slots_ordered,
    utcnow_naive,
)
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.site_models import LeagueDraft, LeagueDraftPick, LeagueDraftQueueItem, LeagueDraftSoundbite

draft_hub_bp = Blueprint("draft_hub", __name__, url_prefix="/draft-hub")


def _league_slug() -> str:
    return str(current_app.config.get("LEAGUE_SLUG") or "")


def _membership():
    if not current_user.is_authenticated:
        return None
    return active_membership_for_league(current_user, _league_slug())


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
    summary = {}
    if draft.completed_summary_json:
        try:
            summary = json.loads(draft.completed_summary_json)
        except json.JSONDecodeError:
            summary = {}
    return render_template(
        "draft_hub_archive_one.html",
        draft=draft,
        picks=picks,
        players=players,
        team_by_id=teams,
        summary=summary,
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
    team_by_id = {t.id: t for t in db.session.scalars(select(Team)).all()}

    def _pick_dict(pk: LeagueDraftPick) -> dict:
        pl = db.session.get(Player, pk.player_id)
        tm = team_by_id.get(pk.team_id)
        return {
            "overall": pk.overall_pick,
            "round": pk.round,
            "team": tm.full_display_name() if tm else str(pk.team_id),
            "player": pl.full_name if pl else str(pk.player_id),
            "player_id": pk.player_id,
            "source": pk.source,
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
    order_rows = []
    if slots:
        for i, s in enumerate(slots):
            tm = team_by_id.get(s.team_id)
            order_rows.append(
                {
                    "overall": s.overall_pick,
                    "round": s.round,
                    "team_id": s.team_id,
                    "team": tm.full_display_name() if tm else str(s.team_id),
                    "forfeited": s.forfeited,
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
                current_slot = {"overall": cs.overall_pick, "round": cs.round, "team_id": cs.team_id}
                tm = team_by_id.get(cs.team_id)
                on_clock_team = tm.full_display_name() if tm else str(cs.team_id)

    params = draft_eligibility_params(draft)
    picked = picked_player_ids(db.session, draft.id)
    eligible = eligible_players_ordered(db.session, slug, params)
    eligible = [p for p in eligible if p.id not in picked]
    eligible_count = len(eligible)

    now = utcnow_naive()
    deadline_ms = None
    if draft.pick_deadline_at and draft.status == "live" and not draft.awaiting_admin_resolution:
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
        queue_items = [{"id": int(x.id), "player_id": int(x.player_id)} for x in qitems]

    return jsonify(
        {
            "ok": True,
            "draft": {
                "id": draft.id,
                "name": draft.name,
                "status": draft.status,
                "scheduled_start_at": draft.scheduled_start_at.isoformat() if draft.scheduled_start_at else None,
                "awaiting_admin": bool(draft.awaiting_admin_resolution),
                "on_clock_team": on_clock_team,
                "current_slot": current_slot,
                "deadline_ms": deadline_ms,
                "timer_seconds": draft.timer_seconds,
                "eligible_count": eligible_count,
                "order": order_rows,
                "recent_picks": pick_payload,
                "ticker_picks": ticker_picks,
                "sounds": sounds,
                "queue_player_ids": queue_ids,
                "queue_items": queue_items,
                "can_pick": bool(
                    mem
                    and draft.status == "live"
                    and not draft.awaiting_admin_resolution
                    and current_slot
                    and mem.team_id == current_slot["team_id"]
                    and current_user.is_authenticated
                    and int(current_user.id) in gm_user_ids_for_team(db.session, slug, current_slot["team_id"])
                ),
            },
        }
    )


@draft_hub_bp.get("/api/eligible-page")
def draft_hub_eligible_page():
    slug = _league_slug()
    draft = featured_draft(db.session, slug)
    if not draft:
        return jsonify({"ok": True, "players": []})
    q = (request.args.get("q") or "").strip().lower()
    offset = max(0, request.args.get("offset", type=int) or 0)
    limit = min(80, max(1, request.args.get("limit", type=int) or 40))
    params = draft_eligibility_params(draft)
    picked = picked_player_ids(db.session, draft.id)
    eligible = eligible_players_ordered(db.session, slug, params)
    eligible = [p for p in eligible if p.id not in picked]
    if q:
        eligible = [p for p in eligible if q in (p.full_name or "").lower()]
    slice_ = eligible[offset : offset + limit]
    _, max_d = anchor_dates(params)

    def age_years(bd):
        return age_as_of(bd, max_d)

    out = []
    for pl in slice_:
        rr = get_player_ratings_row(pl.fhm_player_id)
        out.append(
            {
                "id": pl.id,
                "name": pl.full_name,
                "team": pl.current_team.full_display_name() if pl.current_team else "",
                "pos": player_positions_display_label(pl),
                "age": age_years(pl.birth_date),
                "pot": pl.overall_potential,
                "abi": pl.overall_ability,
                "height_in": pl.height_inches,
                "weight_lb": pl.weight_lbs,
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
        else:
            flash_err = record_pick(db.session, draft, int(pid_raw), int(current_user.id), "gm")
    if flash_err:
        flash(flash_err, "err")
    db.session.commit()
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
