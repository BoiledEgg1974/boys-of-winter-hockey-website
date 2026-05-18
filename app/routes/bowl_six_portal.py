"""BOWL Six GM and admin routes."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import select

from app.auth_login import (
    ADMIN_ROLE_LEAGUE,
    ADMIN_ROLE_SUPER,
    active_membership_for_league,
    require_admin_role,
)
from app.league_db import db
from app.models import Player, PlayerSkaterStat, Team
from app.routes.site_portal import site_admin_bp, site_gm_bp
from app.services.bowl_six import (
    AP_PRIZES,
    SLOT_LABELS,
    blocked_player_ids_from_prior_slate,
    bowl_six_enabled,
    get_lineup,
    get_or_create_current_slate,
    gm_season_standings,
    last_scored_slate,
    lineup_is_editable,
    most_picked_for_slate,
    save_lineup,
    score_slate,
    slate_rankings,
    top_players_for_slate,
)
from app.services.bowl_six_scoring import SLOT_ORDER as SCORING_SLOTS
from app.services.gm_messaging import gm_display_name
from app.services.seasons import get_current_season
from app.site_models import AdminAuditLog, BowlSixSlate, User


def _league_slug() -> str:
    from flask import current_app

    return str(current_app.config.get("LEAGUE_SLUG") or "")


def _membership():
    return active_membership_for_league(current_user, _league_slug())


def _can_use_gm_messaging() -> bool:
    if not current_user.is_authenticated:
        return False
    if getattr(current_user, "is_admin", False):
        return True
    return _membership() is not None


def _require_bowl_six_access():
    if not current_user.is_authenticated:
        abort(401)
    if not _can_use_gm_messaging():
        flash("BOWL Six is available to active GMs and league admins.", "err")
        abort(403)


def _audit(admin_action: str, detail: dict) -> None:
    db.session.add(
        AdminAuditLog(
            admin_user_id=int(current_user.id),
            league_slug=_league_slug(),
            action=admin_action,
            detail_json=json.dumps(detail),
        )
    )


@site_gm_bp.get("/bowl-six")
@login_required
def bowl_six_hub():
    _require_bowl_six_access()
    slug = _league_slug()
    if not bowl_six_enabled(db.session, slug):
        flash("BOWL Six is disabled for this league.", "err")
        return redirect(url_for("main.home"))
    slate = get_or_create_current_slate(db.session, slug)
    mem = _membership()
    my_lineup = None
    if mem and slate:
        my_lineup = get_lineup(db.session, slate.id, int(current_user.id))
    last = last_scored_slate(db.session, slug)
    last_ranked = slate_rankings(db.session, last) if last else []
    top_perf_rows = top_players_for_slate(db.session, slate, limit=5) if slate and slate.status != "skipped" else []
    most_picked_rows = most_picked_for_slate(db.session, slate, limit=5) if slate and slate.status != "skipped" else []
    top_perf = [
        {"row": r, "player": db.session.get(Player, int(r.player_id))} for r in top_perf_rows
    ]
    most_picked = [
        {"row": r, "player": db.session.get(Player, int(r.player_id)), "pick_pct": getattr(r, "_pick_pct", 0)}
        for r in most_picked_rows
    ]
    gm_mini = gm_season_standings(db.session, slug)[:10]
    lock_at = slate.lock_at if slate else None
    return render_template(
        "bowl_six/hub.html",
        slate=slate,
        my_lineup=my_lineup,
        last_slate=last,
        last_ranked=last_ranked,
        top_performers=top_perf,
        most_picked=most_picked,
        gm_mini=gm_mini,
        lock_at=lock_at,
        ap_prizes=AP_PRIZES,
        membership=mem,
    )


@site_gm_bp.route("/bowl-six/lineup", methods=["GET", "POST"])
@login_required
def bowl_six_lineup():
    _require_bowl_six_access()
    slug = _league_slug()
    if not bowl_six_enabled(db.session, slug):
        flash("BOWL Six is disabled for this league.", "err")
        return redirect(url_for("main.home"))
    slate = get_or_create_current_slate(db.session, slug)
    if not slate or slate.status == "skipped":
        flash("No active BOWL Six slate this week.", "err")
        return redirect(url_for("site_gm.bowl_six_hub"))
    editable = lineup_is_editable(slate)
    mem = _membership()
    blocked = (
        blocked_player_ids_from_prior_slate(db.session, slug, int(current_user.id), slate)
        if mem
        else set()
    )
    if request.method == "POST":
        if not mem:
            flash("Submitting a lineup requires an active GM membership.", "err")
            return redirect(url_for("site_gm.bowl_six_lineup"))
        picks: dict[str, int] = {}
        for slot in SCORING_SLOTS:
            raw = request.form.get(f"slot_{slot}")
            try:
                picks[slot] = int(raw)
            except (TypeError, ValueError):
                flash(f"Invalid player for {SLOT_LABELS.get(slot, slot)}.", "err")
                return redirect(url_for("site_gm.bowl_six_lineup"))
        cap_raw = request.form.get("captain_player_id")
        captain = int(cap_raw) if cap_raw and str(cap_raw).strip().isdigit() else None
        result = save_lineup(
            db.session,
            db.session,
            league_slug=slug,
            slate=slate,
            user_id=int(current_user.id),
            picks=picks,
            captain_player_id=captain,
        )
        if result.ok:
            db.session.commit()
            flash("Lineup saved.", "ok")
        else:
            db.session.rollback()
            flash(result.message, "err")
        return redirect(url_for("site_gm.bowl_six_lineup"))
    lineup = None
    if mem:
        lineup = get_lineup(db.session, slate.id, int(current_user.id))
    pick_map = {p.slot: int(p.player_id) for p in (lineup.picks if lineup else [])}
    pick_names: dict[str, str] = {}
    for slot, pid in pick_map.items():
        pl = db.session.get(Player, pid)
        pick_names[slot] = pl.full_name if pl else f"#{pid}"
    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    return render_template(
        "bowl_six/lineup.html",
        slate=slate,
        lineup=lineup,
        pick_map=pick_map,
        pick_names=pick_names,
        slots=SCORING_SLOTS,
        slot_labels=SLOT_LABELS,
        editable=editable,
        blocked_ids=list(blocked),
        teams=teams,
        lock_at=slate.lock_at,
    )


@site_gm_bp.get("/bowl-six/leaders")
@login_required
def bowl_six_leaders():
    _require_bowl_six_access()
    slug = _league_slug()
    rows = gm_season_standings(db.session, slug)
    enriched = []
    for r in rows:
        user = db.session.get(User, int(r["user_id"]))
        mem = active_membership_for_league(user, slug)
        team = db.session.get(Team, int(mem.team_id)) if mem else None
        enriched.append(
            {
                **r,
                "gm_name": gm_display_name(user) if user else f"User #{r['user_id']}",
                "team": team,
            }
        )
    return render_template("bowl_six/leaders.html", rows=enriched)


@site_gm_bp.get("/bowl-six/api/players")
@login_required
def bowl_six_api_players():
    _require_bowl_six_access()
    slug = _league_slug()
    slate = get_or_create_current_slate(db.session, slug)
    q = (request.args.get("q") or "").strip().lower()
    team_filter = request.args.get("team_id")
    pos_filter = (request.args.get("position") or "").strip().lower()
    blocked = set()
    if slate and current_user.is_authenticated:
        blocked = blocked_player_ids_from_prior_slate(
            db.session, slug, int(current_user.id), slate
        )
    season = get_current_season()
    if season is None:
        return jsonify({"players": []})
    query = (
        db.session.query(Player, PlayerSkaterStat, Team)
        .join(
            PlayerSkaterStat,
            (PlayerSkaterStat.player_id == Player.id)
            & (PlayerSkaterStat.season_id == season.id)
            & (PlayerSkaterStat.stat_segment == "rs"),
        )
        .outerjoin(Team, Team.id == PlayerSkaterStat.team_id)
    )
    if team_filter and str(team_filter).isdigit():
        query = query.filter(PlayerSkaterStat.team_id == int(team_filter))
    rows = query.limit(500).all()
    out = []
    for player, _st, team in rows:
        name = player.full_name.lower()
        if q and q not in name:
            continue
        from app.services.bowl_six_scoring import position_kind

        pk = position_kind(player.position)
        if pos_filter == "gk" and pk != "gk":
            continue
        if pos_filter == "def" and pk != "def":
            continue
        if pos_filter == "fwd" and pk != "fwd":
            continue
        pid = int(player.id)
        blocked_reason = "Used last slate" if pid in blocked else ""
        out.append(
            {
                "id": pid,
                "name": player.full_name,
                "position": player.position or "",
                "team_id": int(team.id) if team else None,
                "team_name": team.full_display_name() if team else "",
                "blocked": bool(blocked_reason),
                "blocked_reason": blocked_reason,
            }
        )
    out.sort(key=lambda x: x["name"])
    return jsonify({"players": out[:200]})


@site_admin_bp.post("/control-center/bowl-six/score")
@login_required
def admin_bowl_six_score():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    sid = int(request.form.get("slate_id") or "0")
    slate = db.session.get(BowlSixSlate, sid)
    if not slate or slate.league_slug != slug:
        flash("Invalid slate.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    n = score_slate(db.session, db.session, slate)
    db.session.commit()
    _audit("bowl_six_score", {"slate_id": sid, "lineups_scored": n})
    db.session.commit()
    flash(f"BOWL Six slate scored ({n} lineups).", "ok")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/bowl-six/rescore")
@login_required
def admin_bowl_six_rescore():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if (request.form.get("confirm_phrase") or "").strip().upper() != "RESCORE":
        flash("Type RESCORE to confirm re-score.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    sid = int(request.form.get("slate_id") or "0")
    slate = db.session.get(BowlSixSlate, sid)
    if not slate or slate.league_slug != slug:
        flash("Invalid slate.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    n = score_slate(db.session, db.session, slate)
    db.session.commit()
    _audit("bowl_six_rescore", {"slate_id": sid, "lineups_scored": n})
    db.session.commit()
    flash(f"BOWL Six slate re-scored ({n} lineups).", "ok")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/bowl-six/unlock")
@login_required
def admin_bowl_six_unlock():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    if not request.form.get("confirm_unlock"):
        flash("Confirm unlock.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    sid = int(request.form.get("slate_id") or "0")
    slate = db.session.get(BowlSixSlate, sid)
    if not slate or slate.league_slug != slug:
        flash("Invalid slate.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    if slate.status == "scored":
        flash("Cannot unlock a scored slate.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    slate.status = "open"
    db.session.commit()
    _audit("bowl_six_unlock", {"slate_id": sid})
    db.session.commit()
    flash("Slate unlocked for edits.", "ok")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/bowl-six/extend-lock")
@login_required
def admin_bowl_six_extend_lock():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    sid = int(request.form.get("slate_id") or "0")
    slate = db.session.get(BowlSixSlate, sid)
    if not slate or slate.league_slug != slug:
        flash("Invalid slate.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    raw = (request.form.get("lock_at") or "").strip()
    try:
        slate.lock_at = datetime.fromisoformat(raw.replace("Z", ""))
    except ValueError:
        flash("Invalid lock datetime.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    if slate.status == "locked" and datetime.utcnow() < slate.lock_at:
        slate.status = "open"
    db.session.commit()
    _audit("bowl_six_extend_lock", {"slate_id": sid, "lock_at": raw})
    db.session.commit()
    flash("Lock time updated.", "ok")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/bowl-six/skip-week")
@login_required
def admin_bowl_six_skip_week():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    sid = int(request.form.get("slate_id") or "0")
    slate = db.session.get(BowlSixSlate, sid)
    if not slate or slate.league_slug != slug:
        flash("Invalid slate.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    slate.status = "skipped"
    slate.skip_reason = (request.form.get("skip_reason") or "").strip()[:500]
    db.session.commit()
    _audit("bowl_six_skip", {"slate_id": sid})
    db.session.commit()
    flash("Slate marked as skipped (bye week).", "ok")
    return redirect(url_for("site_admin.admin_control_center"))


@site_admin_bp.post("/control-center/bowl-six/advance-week")
@login_required
def admin_bowl_six_advance_week():
    require_admin_role(ADMIN_ROLE_LEAGUE, ADMIN_ROLE_SUPER)
    slug = _league_slug()
    from app.services.bowl_six import _week_bounds_for_date, default_lock_at
    from app.services.league_rules import rule_int

    today = date.today()
    week_start_dow = rule_int(db.session, slug, "bowl_six_week_start_dow", 0)
    ws, we = _week_bounds_for_date(today + timedelta(days=7), week_start_dow)
    if db.session.scalar(
        select(BowlSixSlate)
        .where(BowlSixSlate.league_slug == slug, BowlSixSlate.week_start == ws)
        .limit(1)
    ):
        flash("Next week slate already exists.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    slate = BowlSixSlate(
        league_slug=slug,
        week_start=ws,
        week_end=we,
        lock_at=default_lock_at(ws, slug, db.session),
        status="open",
        label=f"Week of {ws.isoformat()}",
    )
    db.session.add(slate)
    db.session.commit()
    flash("Created next weekly slate.", "ok")
    return redirect(url_for("site_admin.admin_control_center"))
