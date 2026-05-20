"""BOWL Six GM and admin routes."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from pathlib import Path

from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func, select

from app.auth_login import (
    ADMIN_ROLE_LEAGUE,
    ADMIN_ROLE_SUPER,
    active_membership_for_league,
    require_admin_role,
)
from app.league_db import db
from app.models import Player, PlayerGoalieStat, PlayerSkaterStat, Team
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.player_overall_score import build_overall_cell_map_from_players
from app.services.player_ratings_csv import player_positions_display_label
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
    auto_update_bowl_six_slates,
    extend_slate_lock_at,
    save_lineup,
    score_slate,
    slate_lock_ui,
    slate_rankings,
    bowl_six_lineup_snapshot_slots,
    slate_gm_submission_roster_enriched,
    slate_rankings_in_progress,
    slate_week_game_progress,
    top_players_for_slate,
)
from app.services.bowl_six_scoring import SLOT_ORDER as SCORING_SLOTS
from app.services.gm_messaging import gm_display_name
from app.services.seasons import get_current_season
from app.site_models import AdminAuditLog, BowlSixPlayerWeekStat, BowlSixSlate, User


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


BOWL_SIX_POOL_MAX_PLAYERS = 5000


def _pool_name_filter(pattern: str):
    """Case-insensitive name contains (for server-side pool search)."""
    text = (pattern or "").strip()
    if not text:
        return None
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return Player.full_name.ilike(f"%{escaped}%", escape="\\")


def _jinja_filter_style(filter_name: str, val: object) -> str:
    if val is None:
        return ""
    fn = current_app.jinja_env.filters.get(filter_name)
    if fn is None:
        return ""
    try:
        return str(fn(val) or "")
    except (TypeError, ValueError):
        return ""


def _player_headshot_url(player: Player) -> str | None:
    static_root = Path(current_app.root_path) / (current_app.static_folder or "static")
    rel = resolve_player_headshot_static_filename(
        static_root,
        player,
        current_app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
    )
    if not rel:
        return None
    return url_for("static", filename=rel)


def _enrich_gm_leader_rows(slug: str, rows: list[dict], *, points_key: str) -> list[dict]:
    enriched: list[dict] = []
    for r in rows:
        user = db.session.get(User, int(r["user_id"]))
        mem = active_membership_for_league(user, slug) if user else None
        team = db.session.get(Team, int(mem.team_id)) if mem else None
        enriched.append(
            {
                **r,
                "gm_name": gm_display_name(user) if user else f"User #{r['user_id']}",
                "team": team,
                "points": float(r.get(points_key) or 0),
            }
        )
    return enriched


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
    try:
        auto_update_bowl_six_slates(db.session, db.session, slug)
        db.session.commit()
    except Exception:
        db.session.rollback()
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
    lock_ui = slate_lock_ui(slate)
    submissions = None
    if slate and slate.status != "skipped":
        submissions = slate_gm_submission_roster_enriched(db.session, db.session, slug, slate)
    return render_template(
        "bowl_six/hub.html",
        slate=slate,
        my_lineup=my_lineup,
        last_slate=last,
        last_ranked=last_ranked,
        top_performers=top_perf,
        most_picked=most_picked,
        gm_mini=gm_mini,
        lock_ui=lock_ui,
        ap_prizes=AP_PRIZES,
        membership=mem,
        submissions=submissions,
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
            flash("Lineup saved successfully.", "ok")
        else:
            db.session.rollback()
            flash(result.message, "err")
        return redirect(url_for("site_gm.bowl_six_lineup"))
    lineup = None
    if mem:
        lineup = get_lineup(db.session, slate.id, int(current_user.id))
    pick_map = {p.slot: int(p.player_id) for p in (lineup.picks if lineup else [])}
    pick_players: dict[str, dict] = {}
    for slot, pid in pick_map.items():
        pl = db.session.get(Player, pid)
        if pl is None:
            continue
        pick_players[slot] = {
            "id": int(pl.id),
            "name": pl.full_name,
            "positions": player_positions_display_label(pl) or (pl.position or ""),
            "headshot_url": _player_headshot_url(pl),
        }
    teams = list(db.session.scalars(select(Team).order_by(Team.name)).all())
    return render_template(
        "bowl_six/lineup.html",
        slate=slate,
        lineup=lineup,
        pick_map=pick_map,
        pick_players=pick_players,
        slots=SCORING_SLOTS,
        slot_labels=SLOT_LABELS,
        editable=editable,
        blocked_ids=list(blocked),
        teams=teams,
        lock_ui=slate_lock_ui(slate),
    )


@site_gm_bp.get("/bowl-six/leaders")
@login_required
def bowl_six_leaders():
    _require_bowl_six_access()
    slug = _league_slug()
    try:
        auto_update_bowl_six_slates(db.session, db.session, slug)
        db.session.commit()
    except Exception:
        db.session.rollback()
    in_progress_slate = None
    in_progress_rows: list[dict] = []
    week_progress: dict | None = None
    slate = get_or_create_current_slate(db.session, slug)
    if slate and slate.status == "locked":
        in_progress_slate = slate
        in_progress_rows = _enrich_gm_leader_rows(
            slug,
            slate_rankings_in_progress(db.session, slate),
            points_key="total_points",
        )
        week_progress = slate_week_game_progress(db.session, slate)
    season_rows = _enrich_gm_leader_rows(
        slug, gm_season_standings(db.session, slug), points_key="season_points"
    )
    return render_template(
        "bowl_six/leaders.html",
        rows=season_rows,
        in_progress_slate=in_progress_slate,
        in_progress_rows=in_progress_rows,
        week_progress=week_progress,
    )


@site_gm_bp.get("/bowl-six/api/lineup/<int:user_id>")
@login_required
def bowl_six_api_lineup_snapshot(user_id: int):
    _require_bowl_six_access()
    slug = _league_slug()
    slate = get_or_create_current_slate(db.session, slug)
    if not slate or slate.status == "skipped":
        abort(404)
    try:
        slots = bowl_six_lineup_snapshot_slots(db.session, db.session, slate, user_id)
    except Exception:
        current_app.logger.exception(
            "BOWL Six lineup snapshot failed slate=%s user=%s", slate.id, user_id
        )
        abort(500)
    if not slots:
        abort(404)
    forward_slots = [s for s in slots if str(s["slot"]).startswith("fwd")]
    defense_slots = [s for s in slots if str(s["slot"]).startswith("def")]
    goalie_slots = [s for s in slots if str(s["slot"]) == "gk"]
    return render_template(
        "bowl_six/_lineup_snapshot.html",
        forward_slots=forward_slots,
        defense_slots=defense_slots,
        goalie_slots=goalie_slots,
    )


@site_gm_bp.get("/bowl-six/api/players")
@login_required
def bowl_six_api_players():
    from app.services.bowl_six_scoring import position_kind

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
    week_stats: dict[int, BowlSixPlayerWeekStat] = {}
    if slate:
        for row in db.session.scalars(
            select(BowlSixPlayerWeekStat).where(BowlSixPlayerWeekStat.slate_id == slate.id)
        ):
            week_stats[int(row.player_id)] = row
    total_lineups = 0
    if slate:
        from app.site_models import BowlSixLineup

        total_lineups = db.session.scalar(
            select(func.count())
            .select_from(BowlSixLineup)
            .where(
                BowlSixLineup.slate_id == slate.id,
                BowlSixLineup.submitted_at.is_not(None),
            )
        ) or 0

    def pool_row(player: Player, team: Team | None) -> dict | None:
        pk = position_kind(player.position)
        if pos_filter == "gk" and pk != "gk":
            return None
        if pos_filter == "def" and pk != "def":
            return None
        if pos_filter == "fwd" and pk != "fwd":
            return None
        pid = int(player.id)
        blocked_reason = "Used last slate" if pid in blocked else ""
        wk = week_stats.get(pid)
        pick_pct = None
        if wk and total_lineups > 0 and wk.pick_count:
            pick_pct = round(100.0 * int(wk.pick_count) / total_lineups, 1)
        abi = (
            float(player.overall_ability)
            if player.overall_ability is not None
            else None
        )
        pot = (
            float(player.overall_potential)
            if player.overall_potential is not None
            else None
        )
        return {
            "id": pid,
            "name": player.full_name,
            "position": player.position or "",
            "positions": player_positions_display_label(player) or (player.position or ""),
            "position_kind": pk,
            "team_id": int(team.id) if team else None,
            "team_name": team.full_display_name() if team else "",
            "headshot_url": _player_headshot_url(player),
            "fantasy_points": float(wk.fantasy_points) if wk and wk.fantasy_points is not None else None,
            "pick_pct": pick_pct,
            "blocked": bool(blocked_reason),
            "blocked_reason": blocked_reason,
            "abi": abi,
            "pot": pot,
            "_player": player,
        }

    seen: set[int] = set()
    out: list[dict] = []
    name_clause = _pool_name_filter(q)
    skater_q = (
        db.session.query(Player, PlayerSkaterStat, Team)
        .join(
            PlayerSkaterStat,
            (PlayerSkaterStat.player_id == Player.id)
            & (PlayerSkaterStat.season_id == season.id)
            & (PlayerSkaterStat.stat_segment == "rs"),
        )
        .outerjoin(Team, Team.id == PlayerSkaterStat.team_id)
    )
    if name_clause is not None:
        skater_q = skater_q.filter(name_clause)
    if team_filter and str(team_filter).isdigit():
        skater_q = skater_q.filter(PlayerSkaterStat.team_id == int(team_filter))
    skater_q = skater_q.order_by(Player.full_name.asc())
    for player, _st, team in skater_q.limit(BOWL_SIX_POOL_MAX_PLAYERS).all():
        pid = int(player.id)
        if pid in seen:
            continue
        row = pool_row(player, team)
        if row:
            seen.add(pid)
            out.append(row)
    goalie_q = (
        db.session.query(Player, PlayerGoalieStat, Team)
        .join(
            PlayerGoalieStat,
            (PlayerGoalieStat.player_id == Player.id)
            & (PlayerGoalieStat.season_id == season.id)
            & (PlayerGoalieStat.stat_segment == "rs"),
        )
        .outerjoin(Team, Team.id == PlayerGoalieStat.team_id)
    )
    if name_clause is not None:
        goalie_q = goalie_q.filter(name_clause)
    if team_filter and str(team_filter).isdigit():
        goalie_q = goalie_q.filter(PlayerGoalieStat.team_id == int(team_filter))
    goalie_q = goalie_q.order_by(Player.full_name.asc())
    for player, _st, team in goalie_q.limit(BOWL_SIX_POOL_MAX_PLAYERS).all():
        pid = int(player.id)
        if pid in seen:
            continue
        row = pool_row(player, team)
        if row:
            seen.add(pid)
            out.append(row)
    out.sort(key=lambda x: x["name"])
    if out:
        pl_list = [row.pop("_player") for row in out]
        ova_map = build_overall_cell_map_from_players(db.session, pl_list)
        for row in out:
            pid = int(row["id"])
            ova = ova_map.get(pid) or {}
            ovr = ova.get("score")
            row["ovr"] = int(ovr) if ovr is not None else None
            row["ovr_style"] = (
                _jinja_filter_style("attr_rating_style", float(ovr) * 20.0 / 100.0)
                if ovr is not None
                else ""
            )
            row["abi_style"] = _jinja_filter_style("rating_pill_style", row.get("abi"))
            row["pot_style"] = _jinja_filter_style("rating_pill_style", row.get("pot"))
    return jsonify({"players": out})


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
        n = score_slate(db.session, db.session, slate, notify=False)
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
    if sid <= 0:
        flash("Invalid slate.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    ok, msg = extend_slate_lock_at(
        db.session,
        league_slug=slug,
        slate_id=sid,
        lock_date=request.form.get("lock_date") or "",
        lock_time=request.form.get("lock_time") or "00:00",
    )
    if not ok:
        flash(msg, "err")
        return redirect(url_for("site_admin.admin_control_center"))
    try:
        db.session.commit()
        _audit(
            "bowl_six_extend_lock",
            {"slate_id": sid, "lock_at": msg},
        )
        db.session.commit()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("BOWL Six extend lock commit failed")
        flash("Could not save lock time. Try again.", "err")
        return redirect(url_for("site_admin.admin_control_center"))
    flash(f"Lock time updated to {msg}.", "ok")
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
    from app.services.bowl_six import (
        _current_scoring_week_bounds,
        _real_bowl_six_week_bounds,
        default_lock_at,
        utcnow_naive,
    )

    current_ws, _ = _real_bowl_six_week_bounds(utcnow_naive())
    ws = current_ws + timedelta(days=7)
    we = ws + timedelta(days=6)
    scoring_start, scoring_end = _current_scoring_week_bounds(db.session)
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
        scoring_week_start=scoring_start,
        scoring_week_end=scoring_end,
        lock_at=default_lock_at(ws, slug, db.session),
        status="open",
        label=f"Week of {ws.isoformat()}",
    )
    db.session.add(slate)
    db.session.commit()
    flash("Created next weekly slate.", "ok")
    return redirect(url_for("site_admin.admin_control_center"))
