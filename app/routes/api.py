"""JSON API endpoints for search, lazy box scores, homepage summary."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, timedelta

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, url_for
from flask_login import current_user

from app.config import Config
from sqlalchemy import func, select, text

from app.logo_urls import team_logo_url_for_team
from app.services.season_team_logo_bundle import dashboard_team_logo_url
from app.models import (
    Game,
    GameGoalieStat,
    GameSkaterStat,
    HistoryAward,
    LeagueMeta,
    Player,
    PlayerContract,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    ScoringEvent,
    Season,
    Team,
    TeamStanding,
    db,
)
from app.services.all_time_records import bowl_nhl_league_ids, skaters_only_position_clause
from app.services.division_labels import load_division_display_maps
from app.services.homepage_dashboard import (
    build_active_streaks,
    build_around_the_league,
    build_champions_panel,
    build_conf_cutoff_map,
    build_standings_by_division,
    build_stars_windows,
    build_team_momentum_streaks,
    build_trending_players,
    build_trending_teams,
    compute_power_rankings_payload,
    league_calendar_anchor_date,
    pick_game_of_the_night,
    pick_next_game_to_watch,
    special_teams_rows_for_power_rankings,
)
from app.services.power_rank_snapshots import apply_power_rank_trends, select_power_rank_baseline_map
from app.services.homepage_modules import module_sort_order_map, module_visibility_map
from app.services.homepage_ticker import build_homepage_ticker_items
from app.services.postseason_odds import build_postseason_odds_payload
from app.services.playoff_bracket import playoff_bracket_payload
from app.services.game_preview import game_preview_payload
from app.services.player_contract_csv import contract_years_remaining_major
from app.services.player_overall_score import _parse_rating_cell, build_overall_cell_map_from_players
from app.services.player_rating_avgs import goalie_category_averages, skater_category_averages
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.news_engagement import add_article_comment, set_article_vote, viewer_can_react_on_news
from app.services.player_ratings_csv import (
    fhm_abi_pot_float,
    get_player_ratings_row,
    player_positions_display_label,
    position_ratings_display_list,
)
from app.services.seasons import (
    get_current_season,
    season_age_reference_date,
    season_display_label,
    season_with_imported_data_fallback,
)
from app.services.team_hover_preview import build_team_hover_preview_payload
from app.services.discord_events import (
    fetch_pending_events_for_bot,
    mark_event_failed,
    mark_event_sent,
    upsert_bot_heartbeat,
)

api_bp = Blueprint("api", __name__)

_FTS_SAFE = re.compile(r"[^\w\s.-]", re.UNICODE)


def _news_dashboard_viewer():
    return current_user if getattr(current_user, "is_authenticated", False) else None


def _player_photo_url(pl: Player | None) -> str:
    if not pl:
        return ""
    static_root = Path(current_app.root_path) / "static"
    rel = resolve_player_headshot_static_filename(
        static_root,
        pl,
        current_app.config.get("PLAYER_HEADSHOTS_REL_DIR", "players"),
    )
    if not rel:
        return ""
    return url_for("static", filename=rel)


def _fmt_toi(sec: int | None) -> str | None:
    if sec is None:
        return None
    try:
        s = int(sec)
    except (TypeError, ValueError):
        return None
    if s < 0:
        return None
    return f"{s // 60}:{s % 60:02d}"


def _player_age_years(birth_date: date | None, ref_date: date | None) -> int | None:
    if not birth_date:
        return None
    rd = ref_date or date.today()
    years = rd.year - birth_date.year
    if (rd.month, rd.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years if years >= 0 else None


def _is_goalie(player: Player) -> bool:
    raw = (player.position or "").strip().upper().replace("/", " ")
    first = raw.split()[0] if raw else ""
    return first == "G"


def _normalized_scoring_periods(game: Game, events: list[ScoringEvent]) -> dict[int, int]:
    """Map scoring event ids to display periods.

    Legacy imports used ``to_int("OT1") -> 1``, so the OT winner sat in period 1 alongside real
    P1 goals; promoting ``events[-1]`` to OT then mis-tagged a regulation goal. Prefer fixing
    imports with :func:`fhm_scoring_period_to_int`. This fallback only runs when **every** goal
    row is still ``period == 1`` (rare old export) and the game ended in OT, not a shootout.
    """
    period_by_event = {ev.id: int(ev.period or 1) for ev in events}
    if not events or not game.went_to_overtime or game.went_to_shootout:
        return period_by_event
    if any((ev.period or 0) > 3 for ev in events):
        return period_by_event
    total_final_goals = int(game.home_score or 0) + int(game.away_score or 0)
    if total_final_goals <= 0 or len(events) != total_final_goals:
        return period_by_event
    if int(game.home_score or 0) == int(game.away_score or 0):
        return period_by_event
    if not all(int(ev.period or 1) == 1 for ev in events):
        return period_by_event
    period_by_event[events[-1].id] = 4
    return period_by_event


def _effective_team_shots(game: Game, goalie_lines: list[GameGoalieStat]) -> tuple[int | None, int | None]:
    """Prefer derived shots-on-goal from goalie SA totals when available.

    In some imports, ``game.home_shots``/``game.away_shots`` come from summary shot totals that
    do not match true shots on goal. Goalie ``shots_against`` provides reliable SOG totals.
    """
    sa_by_team: dict[int, int] = defaultdict(int)
    for row in goalie_lines:
        if row.team_id is None or row.shots_against is None:
            continue
        sa_by_team[row.team_id] += int(row.shots_against)
    home_sog = sa_by_team.get(game.away_team_id)
    away_sog = sa_by_team.get(game.home_team_id)
    if home_sog is None and away_sog is None:
        return game.home_shots, game.away_shots
    return home_sog if home_sog is not None else game.home_shots, away_sog if away_sog is not None else game.away_shots


def _star_entry(game: Game, fhm_pid: int | None) -> dict | None:
    """Three stars: player name + team in this game (from game skater/goalie lines)."""
    if fhm_pid is None:
        return None
    p = db.session.scalars(
        select(Player).where(Player.fhm_player_id == str(fhm_pid)).limit(1)
    ).first()
    if not p:
        return None
    sk = db.session.scalars(
        select(GameSkaterStat).where(
            GameSkaterStat.game_id == game.id,
            GameSkaterStat.player_id == p.id,
        ).limit(1)
    ).first()
    gk = None
    if not sk:
        gk = db.session.scalars(
            select(GameGoalieStat).where(
                GameGoalieStat.game_id == game.id,
                GameGoalieStat.player_id == p.id,
            ).limit(1)
        ).first()
    tid = sk.team_id if sk else (gk.team_id if gk else None)
    tm = db.session.get(Team, tid) if tid else None
    return {
        "name": p.full_name,
        "player_id": p.id,
        "team_abbr": tm.abbreviation if tm else "",
        "team_slug": tm.slug if tm else "",
        "team_logo_url": team_logo_url_for_team(tm) if tm else "",
    }


def _pct(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return (float(numerator) / float(denominator)) * 100.0


def _rookie_cutoff_date(season: Season) -> date | None:
    if season.start_year:
        return date(season.start_year, 9, 15)
    if season.end_year:
        return date(season.end_year - 1, 9, 15)
    return None


def _is_nhl_style_rookie(prior_gp_by_season: list[int], birth_date: date | None, season: Season) -> bool:
    if not prior_gp_by_season:
        prior_gp_by_season = []
    if any(gp > 25 for gp in prior_gp_by_season):
        return False
    if sum(1 for gp in prior_gp_by_season if gp >= 6) >= 2:
        return False
    if birth_date:
        cutoff = _rookie_cutoff_date(season)
        age = _player_age_years(birth_date, cutoff)
        if age is not None and age >= 26:
            return False
    return True


def _rookie_stat_team_is_bowl_nhl(team: Team | None, league_ids: tuple[int, ...]) -> bool:
    """True when the player's season stat row is assigned to a BOWL/NHL club (excludes minor leagues)."""
    if team is None:
        return False
    lid = team.fhm_league_id
    if lid is None:
        # Legacy rows: NULL league id is the main sim league roster.
        return True
    return int(lid) in league_ids


def _fts_match_pattern(q: str) -> str:
    q = _FTS_SAFE.sub(" ", (q or "").strip())
    parts = []
    for p in q.split():
        clean = re.sub(r"\W+", "", p, flags=re.UNICODE)
        if clean:
            parts.append(clean)
    if not parts:
        return ""
    return " AND ".join(f"full_name:{p}*" for p in parts[:6])


@api_bp.get("/search/players")
def search_players():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"results": []})
    pat = _fts_match_pattern(q)
    ids: list[int] = []
    if pat:
        try:
            rows = db.session.execute(
                text("SELECT player_id FROM player_fts WHERE player_fts MATCH :pat LIMIT 20"),
                {"pat": pat},
            ).fetchall()
            ids = [int(r[0]) for r in rows]
        except Exception:
            ids = []
    if not ids:
        like = f"%{q}%"
        pls = db.session.scalars(
            select(Player).where(Player.full_name.ilike(like)).limit(20)
        ).all()
        ids = [p.id for p in pls]
    players = db.session.scalars(select(Player).where(Player.id.in_(ids))).all()
    by_id = {p.id: p for p in players}
    team_ids = {p.current_team_id for p in players if p.current_team_id}
    teams = {}
    if team_ids:
        for t in db.session.scalars(select(Team).where(Team.id.in_(team_ids))).all():
            teams[t.id] = t
    out = []
    for pid in ids:
        p = by_id.get(pid)
        if not p:
            continue
        tm = teams.get(p.current_team_id) if p.current_team_id else None
        out.append(
            {
                "id": p.id,
                "full_name": p.full_name,
                "position": player_positions_display_label(p),
                "team": tm.name if tm else "",
                "team_abbr": tm.abbreviation if tm else "",
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                "team_slug": tm.slug if tm else "",
            }
        )
    return jsonify({"results": out})


def _hockey_year_label_from_start_year(y: int) -> str:
    return f"{y}-{(y + 1) % 100:02d}"


def _bowl_league_ids_for_career(session) -> tuple[int, ...]:
    lids = bowl_nhl_league_ids(session)
    return lids if lids else (0,)


def _hover_skater_career_yearly(session, player_id: int) -> list[tuple[int, int, int, int, int, int]]:
    """Per FHM ``season_year`` RS totals (rs + retired_rs), BOWL/NHL leagues only."""
    line = PlayerSkaterCareerLine
    lids = _bowl_league_ids_for_career(session)
    stmt = (
        select(
            line.season_year,
            func.coalesce(func.sum(line.gp), 0),
            func.coalesce(func.sum(line.goals), 0),
            func.coalesce(func.sum(line.assists), 0),
            func.coalesce(func.sum(line.pim), 0),
            func.coalesce(func.sum(func.coalesce(line.plus_minus, 0)), 0),
        )
        .where(
            line.player_id == player_id,
            line.career_source.in_(("rs", "retired_rs")),
            line.league_fhm_id.in_(lids),
        )
        .group_by(line.season_year)
        .order_by(line.season_year.desc())
    )
    return [(int(sy), int(gp), int(g), int(a), int(pim), int(pm)) for sy, gp, g, a, pim, pm in session.execute(stmt)]


def _hover_goalie_career_yearly(
    session, player_id: int
) -> list[tuple[int, int, int, int, int, int, int]]:
    line = PlayerGoalieCareerLine
    lids = _bowl_league_ids_for_career(session)
    stmt = (
        select(
            line.season_year,
            func.coalesce(func.sum(line.gp), 0),
            func.coalesce(func.sum(line.wins), 0),
            func.coalesce(func.sum(line.losses), 0),
            func.coalesce(func.sum(line.goals_against), 0),
            func.coalesce(func.sum(line.shots_against), 0),
            func.coalesce(func.sum(line.shutouts), 0),
        )
        .where(
            line.player_id == player_id,
            line.career_source.in_(("rs", "retired_rs")),
            line.league_fhm_id.in_(lids),
        )
        .group_by(line.season_year)
        .order_by(line.season_year.desc())
    )
    return [
        (int(sy), int(gp), int(w), int(l), int(ga), int(sa), int(so))
        for sy, gp, w, l, ga, sa, so in session.execute(stmt)
    ]


def _hover_team_for_skater_season(
    session, player_id: int, season_start_year: int, stat_team_id: int | None
) -> Team | None:
    """Team for a RS season: prefer imported stat row, else dominant BOWL/NHL career line."""
    if stat_team_id:
        t = session.get(Team, int(stat_team_id))
        if t is not None:
            return t
    line = PlayerSkaterCareerLine
    lids = _bowl_league_ids_for_career(session)
    lines = list(
        session.scalars(
            select(line).where(
                line.player_id == player_id,
                line.season_year == int(season_start_year),
                line.career_source.in_(("rs", "retired_rs")),
                line.league_fhm_id.in_(lids),
            )
        ).all()
    )
    if not lines:
        return None
    lines.sort(key=lambda L: (-int(L.gp or 0), int(L.id)))
    for ln in lines:
        if ln.team_id:
            t = session.get(Team, int(ln.team_id))
            if t is not None:
                return t
    for ln in lines:
        tf = getattr(ln, "team_fhm_id", None)
        if tf is None:
            continue
        try:
            fs = str(int(tf))
        except (TypeError, ValueError):
            fs = str(tf).strip()
        if not fs:
            continue
        t = session.scalar(select(Team).where(Team.fhm_team_id == fs).limit(1))
        if t is not None:
            return t
    return None


def _hover_team_for_goalie_season(
    session, player_id: int, season_start_year: int, stat_team_id: int | None
) -> Team | None:
    if stat_team_id:
        t = session.get(Team, int(stat_team_id))
        if t is not None:
            return t
    line = PlayerGoalieCareerLine
    lids = _bowl_league_ids_for_career(session)
    lines = list(
        session.scalars(
            select(line).where(
                line.player_id == player_id,
                line.season_year == int(season_start_year),
                line.career_source.in_(("rs", "retired_rs")),
                line.league_fhm_id.in_(lids),
            )
        ).all()
    )
    if not lines:
        return None
    lines.sort(key=lambda L: (-int(L.gp or 0), int(L.id)))
    for ln in lines:
        if ln.team_id:
            t = session.get(Team, int(ln.team_id))
            if t is not None:
                return t
    for ln in lines:
        tf = getattr(ln, "team_fhm_id", None)
        if tf is None:
            continue
        try:
            fs = str(int(tf))
        except (TypeError, ValueError):
            fs = str(tf).strip()
        if not fs:
            continue
        t = session.scalar(select(Team).where(Team.fhm_team_id == fs).limit(1))
        if t is not None:
            return t
    return None


def _hover_season_team_logo_url(team: Team | None, season_start_year: int) -> str | None:
    """Era-accurate team mark for *season_start_year* (same resolver as dashboards)."""
    if team is None:
        return None
    try:
        return dashboard_team_logo_url(team, int(season_start_year))
    except Exception:
        return None


def _hover_recent_skater_seasons(session, player_id: int) -> list[dict[str, object]]:
    """Up to 3 most recent RS seasons: prefer ``PlayerSkaterStat`` rows, fill earlier years from career CSV."""
    by_year: dict[int, dict[str, object]] = {}
    stat_rows = session.execute(
        select(PlayerSkaterStat, Season)
        .join(Season, PlayerSkaterStat.season_id == Season.id)
        .where(PlayerSkaterStat.player_id == player_id, PlayerSkaterStat.stat_segment == "rs")
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
    ).all()
    for st, sn in stat_rows:
        if sn.start_year is None:
            continue
        y = int(sn.start_year)
        team = _hover_team_for_skater_season(session, player_id, y, int(st.team_id) if st.team_id else None)
        by_year[y] = {
            "season": season_display_label(sn),
            "team_logo_url": _hover_season_team_logo_url(team, y),
            "gp": int(st.gp or 0),
            "goals": int(st.goals or 0),
            "assists": int(st.assists or 0),
            "points": int(st.points or 0),
            "pim": int(st.pim or 0),
            "plus_minus": st.plus_minus,
        }
    for sy, gp, g, a, pim, pm in _hover_skater_career_yearly(session, player_id):
        if sy in by_year:
            continue
        team = _hover_team_for_skater_season(session, player_id, int(sy), None)
        by_year[sy] = {
            "season": _hockey_year_label_from_start_year(sy),
            "team_logo_url": _hover_season_team_logo_url(team, int(sy)),
            "gp": int(gp),
            "goals": int(g),
            "assists": int(a),
            "points": int(g) + int(a),
            "pim": int(pim),
            "plus_minus": int(pm),
        }
    if not by_year:
        return []
    years = sorted(by_year.keys(), reverse=True)[:3]
    return [by_year[y] for y in years]


def _hover_recent_goalie_seasons(session, player_id: int) -> list[dict[str, object]]:
    """Up to 3 most recent RS seasons: prefer ``PlayerGoalieStat`` rows, fill from career CSV."""
    by_year: dict[int, dict[str, object]] = {}
    stat_rows = session.execute(
        select(PlayerGoalieStat, Season)
        .join(Season, PlayerGoalieStat.season_id == Season.id)
        .where(PlayerGoalieStat.player_id == player_id, PlayerGoalieStat.stat_segment == "rs")
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
    ).all()
    for st, sn in stat_rows:
        if sn.start_year is None:
            continue
        y = int(sn.start_year)
        sv = float(st.sv_pct) if st.sv_pct is not None else None
        team = _hover_team_for_goalie_season(session, player_id, y, int(st.team_id) if st.team_id else None)
        by_year[y] = {
            "season": season_display_label(sn),
            "team_logo_url": _hover_season_team_logo_url(team, y),
            "gp": int(st.gp or 0),
            "wins": int(st.wins or 0),
            "losses": int(st.losses or 0),
            "ga": int(st.ga or 0),
            "sa": int(st.sa or 0),
            "sv_pct": round(sv, 3) if sv is not None else None,
            "so": int(st.so or 0),
        }
    for sy, gp, w, l, ga, sa, so in _hover_goalie_career_yearly(session, player_id):
        if sy in by_year:
            continue
        sv_pct: float | None = None
        if int(sa) > 0:
            sv_pct = round((float(sa) - float(ga)) / float(sa), 3)
        team = _hover_team_for_goalie_season(session, player_id, int(sy), None)
        by_year[sy] = {
            "season": _hockey_year_label_from_start_year(sy),
            "team_logo_url": _hover_season_team_logo_url(team, int(sy)),
            "gp": int(gp),
            "wins": int(w),
            "losses": int(l),
            "ga": int(ga),
            "sa": int(sa),
            "sv_pct": sv_pct,
            "so": int(so),
        }
    if not by_year:
        return []
    years = sorted(by_year.keys(), reverse=True)[:3]
    return [by_year[y] for y in years]


_SKATER_SHARE_PAIRS: tuple[tuple[str, str], ...] = (
    ("Skating", "skating"),
    ("Shooting", "shooting"),
    ("Playmaking", "playmaking"),
    ("Defending", "defending"),
    ("Physicality", "physicality"),
    ("Conditioning", "conditioning"),
    ("Character", "character"),
    ("Hockey sense", "hockey_sense"),
    ("Screening", "screening"),
    ("Getting open", "getting_open"),
    ("Passing", "passing"),
    ("Puck handling", "puck_handling"),
    ("Shooting accuracy", "shooting_accuracy"),
    ("Shooting range", "shooting_range"),
    ("Offensive read", "offensive_read"),
    ("Checking", "checking"),
    ("Faceoffs", "faceoffs"),
    ("Hitting", "hitting"),
    ("Positioning", "positioning"),
    ("Shot blocking", "shot_blocking"),
    ("Stickchecking", "stickchecking"),
    ("Defensive read", "defensive_read"),
    ("Aggression", "aggression"),
    ("Bravery", "bravery"),
    ("Determination", "determination"),
    ("Team Player", "teamplayer"),
    ("Leadership", "leadership"),
    ("Temperament", "temperament"),
    ("Professionalism", "professionalism"),
    ("Acceleration", "acceleration"),
    ("Agility", "agility"),
    ("Balance", "balance"),
    ("Speed", "speed"),
    ("Stamina", "stamina"),
    ("Strength", "strength"),
    ("Fighting", "fighting"),
)

_GOALIE_SHARE_LEFT: tuple[tuple[str, str], ...] = (
    ("Positioning", "g_positioning"),
    ("Passing", "g_passing"),
    ("Pokecheck", "g_pokecheck"),
    ("Blocker", "blocker"),
    ("Glove", "glove"),
    ("Rebound", "rebound"),
    ("Recovery", "recovery"),
    ("Puckhandling", "g_puckhandling"),
    ("Low Shots", "low_shots"),
    ("Skating", "g_skating"),
    ("Reflexes", "reflexes"),
)

_GOALIE_SHARE_RIGHT: tuple[tuple[str, str], ...] = (
    ("Aggression", "aggression"),
    ("Mental Toughness", "mental_toughness"),
    ("Determination", "determination"),
    ("Team Player", "teamplayer"),
    ("Leadership", "leadership"),
    ("Stamina", "goalie_stamina"),
    ("Professionalism", "professionalism"),
)


def _fmt_share_rating_value(v: float) -> str:
    if abs(v - round(v)) < 0.05:
        return str(int(round(v)))
    return f"{v:.1f}"


def _share_rating_rows(rr: dict | None, pairs: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for label, key in pairs:
        raw = rr.get(key) if rr else None
        cell = _parse_rating_cell(raw)
        if cell is None:
            out.append({"label": label, "value": "—"})
        else:
            out.append({"label": label, "value": _fmt_share_rating_value(cell)})
    return out


def _skater_share_split_columns(rr: dict | None) -> dict[str, list[dict[str, str]]]:
    rows = _share_rating_rows(rr, _SKATER_SHARE_PAIRS)
    if not rows:
        return {"left": [], "right": []}
    mid = (len(rows) + 1) // 2
    return {"left": rows[:mid], "right": rows[mid:]}


def _goalie_share_split_columns(rr: dict | None) -> dict[str, list[dict[str, str]]]:
    return {
        "left": _share_rating_rows(rr, _GOALIE_SHARE_LEFT),
        "right": _share_rating_rows(rr, _GOALIE_SHARE_RIGHT),
    }


def _avg_goalie_season_toi(minutes_played: int | None, gp: int) -> str | None:
    if minutes_played is None or gp <= 0:
        return None
    try:
        secs = int(round(float(minutes_played) * 60.0 / float(gp)))
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return _fmt_toi(secs)


def _latest_rs_season_stats_share(session, player_id: int, is_goalie: bool) -> dict[str, object] | None:
    if is_goalie:
        row = session.execute(
            select(PlayerGoalieStat, Season)
            .join(Season, PlayerGoalieStat.season_id == Season.id)
            .where(PlayerGoalieStat.player_id == player_id, PlayerGoalieStat.stat_segment == "rs")
            .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
            .limit(1)
        ).first()
        if not row:
            return None
        st, sn = row
        gp = int(st.gp or 0)
        wins = int(st.wins or 0)
        losses = int(st.losses or 0)
        otl = int(st.otl or 0)
        sa = int(st.sa or 0)
        ga = int(st.ga or 0)
        sv = sa - ga if sa else None
        sv_pct = float(st.sv_pct) if st.sv_pct is not None else None
        if sv_pct is None and sa > 0 and sv is not None:
            sv_pct = float(sv) / float(sa)
        gaa = float(st.gaa) if st.gaa is not None else None
        mp = st.minutes_played
        if gaa is None and mp and float(mp) > 0:
            gaa = float(ga) * 60.0 / float(mp)
        gr = float(st.game_rating) if st.game_rating is not None else None
        gs_val = int(st.games_started) if st.games_started is not None else None
        return {
            "season": season_display_label(sn),
            "gp": gp,
            "record": f"{wins}-{losses}-{otl}",
            "gaa": round(gaa, 2) if gaa is not None else None,
            "sv_pct": round(sv_pct, 3) if sv_pct is not None else None,
            "gr": round(gr, 1) if gr is not None else None,
            "gs": gs_val,
            "so": int(st.so or 0),
            "toi_pg": _avg_goalie_season_toi(mp, gp),
            "sa": sa,
            "saves": int(sv) if sv is not None else None,
            "ga": ga,
        }
    row = session.execute(
        select(PlayerSkaterStat, Season)
        .join(Season, PlayerSkaterStat.season_id == Season.id)
        .where(PlayerSkaterStat.player_id == player_id, PlayerSkaterStat.stat_segment == "rs")
        .order_by(Season.start_year.desc().nulls_last(), Season.id.desc())
        .limit(1)
    ).first()
    if not row:
        return None
    st, sn = row
    gp = int(st.gp or 0)
    goals = int(st.goals or 0)
    ast = int(st.assists or 0)
    pts = int(st.points) if st.points is not None else goals + ast
    toi_pg = None
    if st.toi_seconds and gp > 0:
        toi_pg = _fmt_toi(int(round(st.toi_seconds / gp)))
    gr = float(st.game_rating) if st.game_rating is not None else None
    pdo = float(st.pdo) if st.pdo is not None else None
    return {
        "season": season_display_label(sn),
        "gp": gp,
        "goals": goals,
        "assists": ast,
        "points": pts,
        "plus_minus": st.plus_minus,
        "pim": int(st.pim or 0),
        "shots": int(st.shots) if st.shots is not None else None,
        "hits": int(st.hits) if st.hits is not None else None,
        "blocked_shots": int(st.blocked_shots) if st.blocked_shots is not None else None,
        "toi_pg": toi_pg,
        "gr": round(gr, 1) if gr is not None else None,
        "pdo": round(pdo, 1) if pdo is not None else None,
    }


def _contract_payload_for_share(session, player: Player, season: Season | None) -> dict[str, object] | None:
    c = session.scalars(select(PlayerContract).where(PlayerContract.player_id == player.id).limit(1)).first()
    if not c:
        return None
    raw_dir = Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
    sy = season.start_year if season else None
    yrs = contract_years_remaining_major(player.fhm_player_id, sy, raw_dir)
    aav = int(c.average_salary) if c.average_salary is not None else None
    if aav is None and yrs is None:
        return {"aav": None, "years_left": None}
    return {"aav": aav, "years_left": yrs}


@api_bp.get("/player/<int:player_id>/hover-card")
def player_hover_card(player_id: int):
    player = db.session.get(Player, player_id)
    if not player:
        return jsonify({"error": "not found"}), 404
    team = db.session.get(Team, player.current_team_id) if player.current_team_id else None
    season = get_current_season()
    age = _player_age_years(player.birth_date, season_age_reference_date(season) if season else None)
    rr = get_player_ratings_row(player.fhm_player_id)
    is_goalie = _is_goalie(player)
    if is_goalie:
        avgs = goalie_category_averages(rr)
        attrs = {
            "goa": int(round(avgs["goa"])) if avgs.get("goa") is not None else None,
            "men": int(round(avgs["men"])) if avgs.get("men") is not None else None,
        }
    else:
        avgs = skater_category_averages(rr)
        attrs = {
            "off": int(round(avgs["off"])) if avgs.get("off") is not None else None,
            "def": int(round(avgs["def"])) if avgs.get("def") is not None else None,
            "phy": int(round(avgs["phy"])) if avgs.get("phy") is not None else None,
            "men": int(round(avgs["men"])) if avgs.get("men") is not None else None,
        }
    ovr_map = build_overall_cell_map_from_players(db.session, [player])
    ova = ovr_map.get(player.id) or {}
    ovr_score = ova.get("score")
    ovr_int = int(ovr_score) if ovr_score is not None else None

    retired = bool(getattr(player, "retired", False))
    abi_f: float | None = float(player.overall_ability) if player.overall_ability is not None else None
    pot_f: float | None = float(player.overall_potential) if player.overall_potential is not None else None
    # Retired / historical players often have null DB ABI–POT but FHM CSV still has ability/potential.
    if rr:
        if abi_f is None:
            abi_f = fhm_abi_pot_float(rr.get("ability"))
        if pot_f is None:
            pot_f = fhm_abi_pot_float(rr.get("potential"))
    if retired:
        recent: list[dict[str, object]] = []
        recent_role = "skater"
    elif is_goalie:
        recent = _hover_recent_goalie_seasons(db.session, player.id)
        recent_role = "goalie"
    else:
        recent = _hover_recent_skater_seasons(db.session, player.id)
        recent_role = "skater"

    rating_share = _goalie_share_split_columns(rr) if is_goalie else _skater_share_split_columns(rr)
    latest_stats = None if retired else _latest_rs_season_stats_share(db.session, player.id, is_goalie)
    contract_payload = _contract_payload_for_share(db.session, player, season)
    league_display = str(current_app.config.get("LEAGUE_DISPLAY_NAME", "") or "").strip()
    position_ratings = position_ratings_display_list(rr) if rr else []

    return jsonify(
        {
            "id": player.id,
            "name": player.full_name or "",
            "player_ovr": ovr_int,
            "retired": retired,
            "position": player_positions_display_label(player),
            "team_abbr": team.abbreviation if team else "",
            "team_name": team.full_display_name() if team else "",
            "team_logo_url": team_logo_url_for_team(team) if team else "",
            "nationality": (player.nationality or "").strip(),
            "league_display_name": league_display,
            "age": age,
            "shoots": (player.shoots_catches or "").strip(),
            "height_inches": player.height_inches,
            "weight_lbs": player.weight_lbs,
            "abi": abi_f,
            "pot": pot_f,
            "is_goalie": is_goalie,
            "attrs": attrs,
            "photo_url": _player_photo_url(player),
            "recent_seasons_role": recent_role,
            "recent_seasons": recent,
            "rating_columns": rating_share,
            "position_ratings": position_ratings,
            "latest_season_stats": latest_stats,
            "contract": contract_payload,
        }
    )


@api_bp.get("/team-hover-preview")
def team_hover_preview():
    slug = (request.args.get("slug") or "").strip()
    if not slug:
        return jsonify({"error": "slug required"}), 400
    canonical = get_current_season()
    season = season_with_imported_data_fallback(db.session, canonical) if canonical else None
    payload = build_team_hover_preview_payload(db.session, slug, season)
    if not payload:
        return jsonify({"error": "not found"}), 404
    league_display = str(current_app.config.get("LEAGUE_DISPLAY_NAME", "") or "").strip()
    payload["league_display_name"] = league_display
    return jsonify(payload)


@api_bp.get("/game/<int:game_id>/boxscore")
def game_boxscore(game_id: int):
    game = db.session.get(Game, game_id)
    if not game:
        return jsonify({"error": "not found"}), 404
    home = db.session.get(Team, game.home_team_id)
    away = db.session.get(Team, game.away_team_id)
    away_id = game.away_team_id
    home_id = game.home_team_id

    goals_by_period_away: defaultdict[int, int] = defaultdict(int)
    goals_by_period_home: defaultdict[int, int] = defaultdict(int)

    scoring_events = list(game.scoring_events or [])
    display_period_by_event = _normalized_scoring_periods(game, scoring_events)

    goals = []
    for ev in scoring_events:
        disp_period = display_period_by_event.get(ev.id, int(ev.period or 1))
        def pname(pid):
            if not pid:
                return None
            pl = db.session.get(Player, pid)
            return pl.full_name if pl else None

        if ev.scoring_team_id == away_id:
            goals_by_period_away[disp_period] += 1
        elif ev.scoring_team_id == home_id:
            goals_by_period_home[disp_period] += 1

        st_team = db.session.get(Team, ev.scoring_team_id) if ev.scoring_team_id else None
        goals.append(
            {
                "period": disp_period,
                "time": ev.time_elapsed,
                "scorer": pname(ev.scorer_player_id),
                "scorer_id": ev.scorer_player_id,
                "a1": pname(ev.assist1_player_id),
                "a2": pname(ev.assist2_player_id),
                "strength": ev.strength,
                "team_abbr": st_team.abbreviation if st_team else "",
                "team_slug": st_team.slug if st_team else "",
                "team_logo_url": team_logo_url_for_team(st_team) if st_team else "",
            }
        )

    max_period = max(list(goals_by_period_away.keys()) + list(goals_by_period_home.keys()) + [0])
    period_columns = []
    for p in range(1, 4):
        period_columns.append(
            {
                "label": str(p),
                "away": goals_by_period_away[p],
                "home": goals_by_period_home[p],
            }
        )
    if max_period > 3:
        ota = sum(c for pp, c in goals_by_period_away.items() if pp > 3)
        oth = sum(c for pp, c in goals_by_period_home.items() if pp > 3)
        period_columns.append({"label": "OT", "away": ota, "home": oth})
    skaters = []
    for row in game.skater_lines:
        pl = db.session.get(Player, row.player_id)
        if not pl:
            continue
        tm = db.session.get(Team, row.team_id)
        skaters.append(
            {
                "player_id": pl.id,
                "player": pl.full_name,
                "team_abbr": tm.abbreviation if tm else "",
                "team_slug": tm.slug if tm else "",
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                "g": row.goals,
                "a": row.assists,
                "s": row.shots,
                "pim": row.pim,
                "plus_minus": row.plus_minus,
                "hits": row.hits,
                "bs": row.blocked_shots,
                "toi": _fmt_toi(row.toi_seconds),
                "gr": row.game_rating,
            }
        )
    skaters.sort(key=lambda x: (-x["g"], -x["a"], x["player"]))
    goalie_lines = list(game.goalie_lines or [])
    goalies = []
    for row in goalie_lines:
        pl = db.session.get(Player, row.player_id)
        if not pl:
            continue
        tm = db.session.get(Team, row.team_id)
        sa = row.shots_against
        sv = row.saves
        sv_pct = round(100.0 * sv / sa, 3) if sa else None
        goalies.append(
            {
                "player_id": pl.id,
                "player": pl.full_name,
                "team_abbr": tm.abbreviation if tm else "",
                "team_slug": tm.slug if tm else "",
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                "saves": sv,
                "sa": sa,
                "ga": row.goals_allowed,
                "sv_pct": sv_pct,
                "decision": row.decision,
                "toi": _fmt_toi(row.toi_seconds),
                "gr": row.game_rating,
            }
        )
    home_shots, away_shots = _effective_team_shots(game, goalie_lines)

    return jsonify(
        {
            "game_id": game.id,
            "date": game.game_date.isoformat() if game.game_date else None,
            "status": game.status,
            "game_type": game.game_type,
            "arena": game.arena,
            "attendance": game.attendance,
            "pim_home": game.pim_home,
            "pim_away": game.pim_away,
            "period_columns": period_columns,
            "special_teams": {
                "home_pp": f"{game.pp_goals_home or 0}/{game.pp_opp_home or 0}"
                if game.pp_opp_home
                else None,
                "away_pp": f"{game.pp_goals_away or 0}/{game.pp_opp_away or 0}"
                if game.pp_opp_away
                else None,
                "pim": f"{game.pim_home or 0}–{game.pim_away or 0}"
                if game.pim_home is not None
                else None,
                "hits": f"{game.hits_home or 0}–{game.hits_away or 0}"
                if game.hits_home is not None
                else None,
            },
            "stars": [
                _star_entry(game, game.fhm_star1_player_id),
                _star_entry(game, game.fhm_star2_player_id),
                _star_entry(game, game.fhm_star3_player_id),
            ],
            "home": {
                "abbr": home.abbreviation if home else "",
                "slug": home.slug if home else "",
                "name": home.name if home else "",
                "score": game.home_score,
                "shots": home_shots,
                "logo_url": team_logo_url_for_team(home) if home else "",
            },
            "away": {
                "abbr": away.abbreviation if away else "",
                "slug": away.slug if away else "",
                "name": away.name if away else "",
                "score": game.away_score,
                "shots": away_shots,
                "logo_url": team_logo_url_for_team(away) if away else "",
            },
            "goals": goals,
            "skaters": skaters[:50],
            "goalies": goalies,
        }
    )


@api_bp.get("/game/<int:game_id>/preview")
def game_preview(game_id: int):
    payload = game_preview_payload(game_id)
    if payload is None:
        return jsonify({"error": "not found"}), 404
    if payload.get("error") == "final":
        return jsonify(payload), 400
    return jsonify(payload)


def _misc_statistics_panel(special_teams: list[dict[str, object]]) -> dict[str, object] | None:
    """Hits, blocks, and faceoff leaders for the homepage Misc. Statistics card (all leagues)."""
    if not special_teams:
        return None
    style_rows = sorted(
        [r for r in special_teams if r.get("gp")],
        key=lambda x: int(x.get("hits") or 0),
        reverse=True,
    )
    top_hits = style_rows[0] if style_rows else None
    top_blocks = max(special_teams, key=lambda x: int(x.get("blocks") or 0), default=None)
    fo_candidates = [r for r in special_teams if r.get("fo_pct") is not None]
    top_fo = max(fo_candidates, key=lambda x: float(x.get("fo_pct") or 0)) if fo_candidates else None

    def pack_row(label: str, row: dict[str, object] | None, detail: str) -> dict[str, object]:
        if not row:
            return {
                "label": label,
                "detail": detail,
                "team_slug": "",
                "team_logo_url": "",
                "team_name": "",
                "value": "—",
            }
        return {
            "label": label,
            "detail": detail,
            "team_slug": str(row.get("team_slug") or ""),
            "team_logo_url": str(row.get("team_logo_url") or ""),
            "team_name": str(row.get("team_name") or ""),
            "value": str(row.get("team") or "—"),
        }

    return {
        "title": "Misc. Statistics",
        "items": [
            pack_row(
                "Most physical",
                top_hits,
                f"{int(top_hits.get('hits') or 0)} hits" if top_hits else "",
            ),
            pack_row(
                "Shot-blocking leader",
                top_blocks,
                f"{int(top_blocks.get('blocks') or 0)} blocks" if top_blocks else "",
            ),
            pack_row(
                "Best faceoff team",
                top_fo,
                f"{float(top_fo.get('fo_pct') or 0):.1f}% FO" if top_fo else "",
            ),
        ],
    }


@api_bp.get("/homepage/summary")
def homepage_summary():
    segment = request.args.get("segment", "rs") or "rs"
    if segment not in ("rs", "ps", "po"):
        segment = "rs"
    canonical_season = get_current_season()
    lm = db.session.scalars(
        select(LeagueMeta).where(LeagueMeta.fhm_league_id == 0).limit(1)
    ).first() or db.session.scalars(select(LeagueMeta).limit(1)).first()
    league_info = (
        {"name": lm.name, "abbr": lm.abbreviation or ""} if lm else {"name": "", "abbr": ""}
    )
    if not canonical_season:
        empty_news = build_around_the_league(db.session, _news_dashboard_viewer())
        empty_body: dict[str, object] = {
            "league_calendar_date": None,
            "teams": [],
            "standings_by_division": [],
            "game_of_the_night": None,
            "next_game_to_watch": None,
            "stars_last_7d": [],
            "stars_last_14d": [],
            "stars_last_30d": [],
            "trending_players": {"hot": [], "cold": []},
            "team_momentum": {
                "trending": {"hot": [], "cold": []},
                "streaks": {
                    "win_streak": [],
                    "undefeated_streak": [],
                    "losing_streak": [],
                    "winless_streak": [],
                },
            },
            "active_streaks": {"goal_streak": [], "point_streak": []},
            "power_rankings": {"teams": [], "top5": [], "bottom5": []},
            "module_settings": {
                "visibility": module_visibility_map(db.session, str(current_app.config.get("LEAGUE_SLUG") or "")),
                "sort_order": module_sort_order_map(db.session, str(current_app.config.get("LEAGUE_SLUG") or "")),
            },
            "champions_panel": {"banner_urls": [], "recent_champions": []},
            "around_the_league": empty_news,
            "leaders": {
                "goals": [],
                "assists": [],
                "points": [],
                "goalie_wins": [],
                "goalie_shutouts": [],
            },
            "games": [],
            "upcoming": [],
            "special_teams": [],
            "rookies": {"skaters": [], "goalies": [], "criteria": {}},
            "league_spotlight": {"title": "", "items": []},
            "identity_panel": None,
            "postseason_odds": None,
            "league": league_info,
            "segment": segment,
        }
        empty_body["ticker_items"] = build_homepage_ticker_items(empty_body)
        return jsonify(empty_body)
    season = season_with_imported_data_fallback(db.session, canonical_season)
    logo_sy: int | None = int(season.start_year) if getattr(season, "start_year", None) is not None else None
    teams_out: list[dict[str, object]] = []

    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    bowl_main_fhm_league_ids: tuple[int, ...] | None = None
    if league_slug in ("bowl-fantasy", "bowl-historical", "bowl-cap"):
        bowl_main_fhm_league_ids = bowl_nhl_league_ids(db.session)
        if not bowl_main_fhm_league_ids:
            bowl_main_fhm_league_ids = (0,)

    def leader_rows(stat, order_col, limit=10, goalie=False):
        if goalie:
            q = select(PlayerGoalieStat, Player).join(
                Player, PlayerGoalieStat.player_id == Player.id
            )
            if bowl_main_fhm_league_ids is not None:
                q = q.join(Team, PlayerGoalieStat.team_id == Team.id).where(
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == segment,
                    Team.fhm_league_id.in_(bowl_main_fhm_league_ids),
                )
            else:
                q = q.where(
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == segment,
                )
            q = q.order_by(order_col.desc(), Player.id.asc()).limit(limit)
            rows = db.session.execute(q).all()
            out = []
            for pgs, pl in rows:
                tm = db.session.get(Team, pgs.team_id) if pgs.team_id else None
                out.append(
                    {
                        "player_id": pl.id,
                        "player": pl.full_name,
                        "player_photo_url": _player_photo_url(pl),
                        "team": tm.abbreviation if tm else "",
                        "team_slug": tm.slug if tm else "",
                        "team_logo_url": dashboard_team_logo_url(tm, logo_sy) if tm else "",
                        "value": getattr(pgs, order_col.key),
                    }
                )
            return out
        q = select(PlayerSkaterStat, Player).join(
            Player, PlayerSkaterStat.player_id == Player.id
        )
        if bowl_main_fhm_league_ids is not None:
            q = q.join(Team, PlayerSkaterStat.team_id == Team.id).where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.stat_segment == segment,
                Team.fhm_league_id.in_(bowl_main_fhm_league_ids),
                skaters_only_position_clause(),
            )
        else:
            q = q.where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.stat_segment == segment,
                skaters_only_position_clause(),
            )
        q = q.order_by(order_col.desc(), Player.id.asc()).limit(limit)
        rows = db.session.execute(q).all()
        out = []
        for pss, pl in rows:
            tm = db.session.get(Team, pss.team_id) if pss.team_id else None
            val = getattr(pss, stat)
            out.append(
                {
                    "player_id": pl.id,
                    "player": pl.full_name,
                    "player_photo_url": _player_photo_url(pl),
                    "team": tm.abbreviation if tm else "",
                    "team_slug": tm.slug if tm else "",
                    "team_logo_url": dashboard_team_logo_url(tm, logo_sy) if tm else "",
                    "value": val,
                }
            )
        return out

    leaders = {
        "goals": leader_rows("goals", PlayerSkaterStat.goals),
        "assists": leader_rows("assists", PlayerSkaterStat.assists),
        "points": leader_rows("points", PlayerSkaterStat.points),
        "goalie_wins": leader_rows("", PlayerGoalieStat.wins, goalie=True),
        "goalie_shutouts": leader_rows("", PlayerGoalieStat.so, goalie=True),
    }

    standings_by_team = {
        st.team_id: st
        for st in db.session.scalars(
            select(TeamStanding).where(TeamStanding.season_id == season.id)
        ).all()
    }
    special_teams = special_teams_rows_for_power_rankings(
        db.session, season.id, segment, standings_by_team, logo_sy
    )
    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)))
    div_pair, div_by_id = load_division_display_maps(raw_dir / "divisions.csv")
    standings_by_division = build_standings_by_division(
        db.session,
        season.id,
        div_name_by_pair=div_pair,
        div_name_by_id=div_by_id,
        logo_season_year=logo_sy,
    )
    tm_map = {
        tid: t
        for tid in standings_by_team
        if (t := db.session.get(Team, tid)) is not None
    }
    conf_cutoff = build_conf_cutoff_map(db.session, season.id)
    league_cal = league_calendar_anchor_date(db.session, season.id)
    gotn_since = league_cal - timedelta(days=7)
    game_of_the_night = pick_game_of_the_night(
        db.session,
        season.id,
        standings_by_team,
        tm_map,
        conf_cutoff,
        gotn_since,
        logo_season_year=logo_sy,
    )
    next_game_to_watch = pick_next_game_to_watch(
        db.session,
        season.id,
        standings_by_team,
        tm_map,
        conf_cutoff,
        league_cal,
        logo_season_year=logo_sy,
    )
    stars_bundle = build_stars_windows(db.session, season.id, league_cal, logo_season_year=logo_sy)
    trending_players = build_trending_players(
        db.session, season.id, segment, league_cal, logo_season_year=logo_sy
    )
    trending_teams = build_trending_teams(db.session, season.id, league_cal, logo_season_year=logo_sy)
    team_momentum_streaks = build_team_momentum_streaks(db.session, season.id, logo_season_year=logo_sy)
    team_momentum = {"trending": trending_teams, "streaks": team_momentum_streaks}
    active_streaks = build_active_streaks(db.session, season.id, logo_season_year=logo_sy)
    power_rankings = compute_power_rankings_payload(
        db.session,
        season_id=season.id,
        segment=segment,
        logo_season_year=logo_sy,
    )
    baseline = select_power_rank_baseline_map(league_slug, power_rankings["teams"])
    apply_power_rank_trends(power_rankings["teams"], baseline)
    module_settings = {
        "visibility": module_visibility_map(db.session, league_slug),
        "sort_order": module_sort_order_map(db.session, league_slug),
    }
    postseason_odds = build_postseason_odds_payload(db.session, season.id, tm_map)
    champions_panel = build_champions_panel(db.session)
    around_the_league = build_around_the_league(
        db.session, _news_dashboard_viewer(), logo_season_year=logo_sy
    )

    upcoming_games = db.session.scalars(
        select(Game)
        .where(
            Game.season_id == season.id,
            Game.status != "final",
            Game.game_date.is_not(None),
            Game.game_date >= league_cal,
        )
        .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
        .limit(12)
    ).all()
    if not upcoming_games:
        upcoming_games = db.session.scalars(
            select(Game)
            .where(Game.season_id == season.id, Game.status != "final")
            .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
            .limit(12)
        ).all()
    upcoming_out: list[dict[str, object]] = []
    for g in upcoming_games:
        ht = db.session.get(Team, g.home_team_id)
        at = db.session.get(Team, g.away_team_id)
        upcoming_out.append(
            {
                "id": g.id,
                "date": g.game_date.isoformat() if g.game_date else None,
                "status": g.status or "",
                "home_name": ht.name if ht else "",
                "away_name": at.name if at else "",
                "home_abbr": ht.abbreviation if ht else "",
                "away_abbr": at.abbreviation if at else "",
                "home_logo_url": dashboard_team_logo_url(ht, logo_sy) if ht else "",
                "away_logo_url": dashboard_team_logo_url(at, logo_sy) if at else "",
                "home_slug": ht.slug if ht else "",
                "away_slug": at.slug if at else "",
            }
        )

    # NHL rookie-eligibility style:
    #  - no prior season with >25 GP
    #  - cannot have played in 6+ games in each of two prior seasons
    #  - must be under 26 on Sep 15 before season start
    max_gp = max((int(st.standing_gp_display() or 0) for st in standings_by_team.values()), default=0)
    rs_skater_abs = int(current_app.config.get("ROOKIE_RS_SKATER_MIN_GP_ABS", 10) or 10)
    rs_skater_pct = float(current_app.config.get("ROOKIE_RS_SKATER_MIN_GP_PCT", 0.20) or 0.20)
    rs_goalie_abs = int(current_app.config.get("ROOKIE_RS_GOALIE_MIN_MINUTES_ABS", 600) or 600)
    rs_goalie_pct = float(current_app.config.get("ROOKIE_RS_GOALIE_MIN_MINUTES_PCT", 0.20) or 0.20)
    pspo_skater = int(current_app.config.get("ROOKIE_PSPO_SKATER_MIN_GP", 2) or 2)
    pspo_goalie = int(current_app.config.get("ROOKIE_PSPO_GOALIE_MIN_MINUTES", 120) or 120)
    if segment == "rs":
        rookie_skater_min_gp = max(rs_skater_abs, int(round(max_gp * rs_skater_pct))) if max_gp > 0 else rs_skater_abs
        rookie_goalie_min_minutes = (
            max(rs_goalie_abs, int(round(max_gp * 60 * rs_goalie_pct))) if max_gp > 0 else rs_goalie_abs
        )
    else:
        rookie_skater_min_gp = pspo_skater
        rookie_goalie_min_minutes = pspo_goalie
    rookies = {
        "criteria": {
            "skater_min_gp": rookie_skater_min_gp,
            "goalie_min_minutes": rookie_goalie_min_minutes,
            "rank_mode": "Calder-style P/GP (skaters), SV% then wins (goalies)",
        },
        "skaters": [],
        "goalies": [],
    }
    # Require min GP in SQL so low-scoring rookies are not dropped by a points-only LIMIT.
    rookie_skaters = db.session.execute(
        select(PlayerSkaterStat, Player)
        .join(Player, PlayerSkaterStat.player_id == Player.id)
        .where(
            PlayerSkaterStat.season_id == season.id,
            PlayerSkaterStat.stat_segment == segment,
            PlayerSkaterStat.gp >= rookie_skater_min_gp,
        )
        .order_by(PlayerSkaterStat.points.desc(), PlayerSkaterStat.goals.desc())
        .limit(1500)
    ).all()
    skater_ids = [pl.id for _, pl in rookie_skaters]
    rookie_league_ids = bowl_nhl_league_ids(db.session) or (0,)
    current_skater_year = db.session.scalar(
        select(func.max(PlayerSkaterCareerLine.season_year)).where(
            PlayerSkaterCareerLine.player_id.in_(skater_ids) if skater_ids else False,
            PlayerSkaterCareerLine.career_source == "rs",
            PlayerSkaterCareerLine.league_fhm_id.in_(rookie_league_ids) if rookie_league_ids else True,
        )
    )
    if current_skater_year is None:
        current_skater_year = (season.start_year - 1) if season.start_year else season.end_year
    prior_skater_lines = db.session.execute(
        select(
            PlayerSkaterCareerLine.player_id,
            PlayerSkaterCareerLine.season_year,
            PlayerSkaterCareerLine.gp,
        ).where(
            PlayerSkaterCareerLine.player_id.in_(skater_ids) if skater_ids else False,
            PlayerSkaterCareerLine.career_source.in_(("rs", "retired_rs")),
            PlayerSkaterCareerLine.league_fhm_id.in_(rookie_league_ids) if rookie_league_ids else True,
            PlayerSkaterCareerLine.season_year < int(current_skater_year or 0),
        )
    ).all()
    prior_skater_gp_by_season: dict[int, dict[int, int]] = defaultdict(dict)
    for pid, season_year, gp in prior_skater_lines:
        pid_i = int(pid)
        yr_i = int(season_year or 0)
        prior_skater_gp_by_season[pid_i][yr_i] = prior_skater_gp_by_season[pid_i].get(yr_i, 0) + int(gp or 0)
    for pss, pl in rookie_skaters:
        prior_gps = list(prior_skater_gp_by_season.get(pl.id, {}).values())
        if not _is_nhl_style_rookie(prior_gps, pl.birth_date, season):
            continue
        gp = int(pss.gp or 0)
        if gp < rookie_skater_min_gp:
            continue
        tm = db.session.get(Team, pss.team_id) if pss.team_id else None
        if not _rookie_stat_team_is_bowl_nhl(tm, rookie_league_ids):
            continue
        ppg = (float(pss.points or 0) / float(gp)) if gp > 0 else 0.0
        rookies["skaters"].append(
            {
                "player_id": pl.id,
                "player": pl.full_name,
                "player_photo_url": _player_photo_url(pl),
                "team": tm.abbreviation if tm else "",
                "team_slug": tm.slug if tm else "",
                "team_logo_url": dashboard_team_logo_url(tm, logo_sy) if tm else "",
                "gp": gp,
                "goals": pss.goals,
                "assists": pss.assists,
                "points": pss.points,
                "ppg": round(ppg, 3),
            }
        )
    rookies["skaters"].sort(
        key=lambda r: (float(r.get("ppg") or 0.0), int(r.get("points") or 0), int(r.get("goals") or 0)),
        reverse=True,
    )
    rookies["skaters"] = rookies["skaters"][:10]

    rookie_goalies = db.session.execute(
        select(PlayerGoalieStat, Player)
        .join(Player, PlayerGoalieStat.player_id == Player.id)
        .where(
            PlayerGoalieStat.season_id == season.id,
            PlayerGoalieStat.stat_segment == segment,
            PlayerGoalieStat.minutes_played >= rookie_goalie_min_minutes,
        )
        .order_by(PlayerGoalieStat.sv_pct.desc(), PlayerGoalieStat.wins.desc())
        .limit(800)
    ).all()
    goalie_ids = [pl.id for _, pl in rookie_goalies]
    current_goalie_year = db.session.scalar(
        select(func.max(PlayerGoalieCareerLine.season_year)).where(
            PlayerGoalieCareerLine.player_id.in_(goalie_ids) if goalie_ids else False,
            PlayerGoalieCareerLine.career_source == "rs",
            PlayerGoalieCareerLine.league_fhm_id.in_(rookie_league_ids) if rookie_league_ids else True,
        )
    )
    if current_goalie_year is None:
        current_goalie_year = current_skater_year
    prior_goalie_lines = db.session.execute(
        select(
            PlayerGoalieCareerLine.player_id,
            PlayerGoalieCareerLine.season_year,
            PlayerGoalieCareerLine.gp,
        ).where(
            PlayerGoalieCareerLine.player_id.in_(goalie_ids) if goalie_ids else False,
            PlayerGoalieCareerLine.career_source.in_(("rs", "retired_rs")),
            PlayerGoalieCareerLine.league_fhm_id.in_(rookie_league_ids) if rookie_league_ids else True,
            PlayerGoalieCareerLine.season_year < int(current_goalie_year or 0),
        )
    ).all()
    prior_goalie_gp_by_season: dict[int, dict[int, int]] = defaultdict(dict)
    for pid, season_year, gp in prior_goalie_lines:
        pid_i = int(pid)
        yr_i = int(season_year or 0)
        prior_goalie_gp_by_season[pid_i][yr_i] = prior_goalie_gp_by_season[pid_i].get(yr_i, 0) + int(gp or 0)
    for pgs, pl in rookie_goalies:
        prior_gps = list(prior_goalie_gp_by_season.get(pl.id, {}).values())
        if not _is_nhl_style_rookie(prior_gps, pl.birth_date, season):
            continue
        minutes = int(pgs.minutes_played or 0)
        if minutes < rookie_goalie_min_minutes:
            continue
        tm = db.session.get(Team, pgs.team_id) if pgs.team_id else None
        if not _rookie_stat_team_is_bowl_nhl(tm, rookie_league_ids):
            continue
        rookies["goalies"].append(
            {
                "player_id": pl.id,
                "player": pl.full_name,
                "player_photo_url": _player_photo_url(pl),
                "team": tm.abbreviation if tm else "",
                "team_slug": tm.slug if tm else "",
                "team_logo_url": dashboard_team_logo_url(tm, logo_sy) if tm else "",
                "gp": pgs.gp,
                "minutes": minutes,
                "wins": pgs.wins,
                "so": int(pgs.so or 0),
                "sv_pct": round(float(pgs.sv_pct or 0), 3) if pgs.sv_pct is not None else None,
                "gaa": round(float(pgs.gaa or 0), 2) if pgs.gaa is not None else None,
            }
        )
    rookies["goalies"].sort(
        key=lambda r: (float(r.get("sv_pct") or 0.0), int(r.get("wins") or 0), int(r.get("minutes") or 0)),
        reverse=True,
    )
    rookies["goalies"] = rookies["goalies"][:50]

    identity_panel = _misc_statistics_panel(special_teams)
    league_spotlight: dict[str, object] = {"title": "League spotlight", "items": []}
    if league_slug == "bowl-fantasy":
        league_spotlight = {"title": "", "items": []}
    elif league_slug == "bowl-cap":
        cap_hits = db.session.execute(
            select(PlayerContract, Player)
            .join(Player, PlayerContract.player_id == Player.id)
            .where(Player.current_team_id.is_not(None))
            .order_by(PlayerContract.average_salary.desc().nulls_last())
            .limit(3)
        ).all()
        ufa_count = db.session.scalar(
            select(func.count(PlayerContract.id))
            .join(Player, PlayerContract.player_id == Player.id)
            .where(Player.current_team_id.is_not(None), PlayerContract.is_ufa.is_(True))
        ) or 0
        cap_lines: list[dict[str, object]] = []
        for contract, pl in cap_hits:
            tm = db.session.get(Team, pl.current_team_id) if pl.current_team_id else None
            cap_lines.append(
                {
                    "player": pl.full_name,
                    "salary": int(contract.average_salary or 0),
                    "team_slug": tm.slug if tm else "",
                    "team_logo_url": dashboard_team_logo_url(tm, logo_sy) if tm else "",
                    "team_name": tm.full_display_name() if tm else "Free agent",
                }
            )
        league_spotlight = {
            "title": "Cap Pressure Board",
            "format": "cap_logos",
            "items": [
                {"label": "Top cap hits", "cap_lines": cap_lines},
                {
                    "label": "Current UFAs",
                    "value": f"{int(ufa_count)}",
                    "detail": "players with UFA flag",
                },
            ],
        }

    games = (
        db.session.scalars(
            select(Game)
            .where(Game.season_id == season.id, Game.status == "final")
            .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
            .limit(12)
        )
        .all()
    )
    games_out = []
    for g in games:
        ht = db.session.get(Team, g.home_team_id)
        at = db.session.get(Team, g.away_team_id)
        games_out.append(
            {
                "id": g.id,
                "date": g.game_date.isoformat() if g.game_date else None,
                "status": "final",
                "home_abbr": ht.abbreviation if ht else "",
                "away_abbr": at.abbreviation if at else "",
                "home_name": ht.name if ht else "",
                "away_name": at.name if at else "",
                "home_score": g.home_score,
                "away_score": g.away_score,
                "game_type": g.game_type or "",
                "home_logo_url": dashboard_team_logo_url(ht, logo_sy) if ht else "",
                "away_logo_url": dashboard_team_logo_url(at, logo_sy) if at else "",
                "home_slug": ht.slug if ht else "",
                "away_slug": at.slug if at else "",
            }
        )

    summary_body: dict[str, object] = {
        "league_calendar_date": league_cal.isoformat(),
        "league_season_label": season_display_label(canonical_season),
        "dashboard_data_season_label": season_display_label(season),
        "dashboard_uses_prior_season_data": bool(
            canonical_season.id != season.id
        ),
        "teams": teams_out,
        "standings_by_division": standings_by_division,
        "game_of_the_night": game_of_the_night,
        "next_game_to_watch": next_game_to_watch,
        "stars_last_7d": stars_bundle.get("stars_last_7d", []),
        "stars_last_14d": stars_bundle.get("stars_last_14d", []),
        "stars_last_30d": stars_bundle.get("stars_last_30d", []),
        "trending_players": trending_players,
        "team_momentum": team_momentum,
        "active_streaks": active_streaks,
        "power_rankings": power_rankings,
        "module_settings": module_settings,
        "champions_panel": champions_panel,
        "around_the_league": around_the_league,
        "leaders": leaders,
        "games": games_out,
        "upcoming": upcoming_out,
        "special_teams": special_teams,
        "rookies": rookies,
        "league_spotlight": league_spotlight,
        "identity_panel": identity_panel,
        "postseason_odds": postseason_odds,
        "league": league_info,
        "segment": segment,
    }
    summary_body["ticker_items"] = build_homepage_ticker_items(summary_body)
    return jsonify(summary_body)


@api_bp.get("/playoff-bracket")
def playoff_bracket():
    """JSON for standings page playoff bracket (single-league, no conferences)."""
    season = get_current_season()
    sid = season.id if season else None
    if request.args.get("season_id"):
        try:
            sid = int(request.args.get("season_id", ""))
        except ValueError:
            sid = season.id if season else None
    return jsonify(playoff_bracket_payload(sid))


def _discord_secret_ok() -> bool:
    expected = str(current_app.config.get("DISCORD_EVENTS_SHARED_SECRET") or "").strip()
    if not expected:
        return False
    presented = str(request.headers.get("X-Discord-Events-Secret") or "").strip()
    return presented == expected


@api_bp.get("/discord/events/pending")
def discord_events_pending():
    if not _discord_secret_ok():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    slug = str(request.args.get("league_slug") or current_app.config.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        return jsonify({"ok": False, "message": "league_slug is required"}), 400
    try:
        limit = int(request.args.get("limit") or "20")
    except ValueError:
        limit = 20
    from app.services.discord_events import bot_event_delivery_fields, get_league_bot_config

    rows = fetch_pending_events_for_bot(db.session, league_slug=slug, limit=limit)
    bot_cfg = get_league_bot_config(db.session, slug)
    out = []
    for r in rows:
        try:
            payload = json.loads(r.payload_json or "{}")
        except Exception:
            payload = {}
        delivery = bot_event_delivery_fields(
            db.session, league_slug=slug, event_key=str(r.event_key or "")
        )
        out.append(
            {
                "id": int(r.id),
                "league_slug": str(r.league_slug or ""),
                "event_key": str(r.event_key or ""),
                "channel_key": str(r.channel_key or ""),
                "discord_channel_id": delivery.get("discord_channel_id") or "",
                "guild_id": delivery.get("guild_id") or str(bot_cfg.guild_id or ""),
                "idempotency_key": str(r.idempotency_key or ""),
                "payload": payload,
                "attempts": int(r.attempts or 0),
                "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else None,
            }
        )
    return jsonify({"ok": True, "events": out, "bot_enabled": bool(bot_cfg.is_enabled)})


@api_bp.post("/discord/events/<int:event_id>/ack")
def discord_events_ack(event_id: int):
    if not _discord_secret_ok():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    ok = mark_event_sent(db.session, event_id)
    return jsonify({"ok": bool(ok)})


@api_bp.post("/discord/events/<int:event_id>/fail")
def discord_events_fail(event_id: int):
    if not _discord_secret_ok():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    error = str(data.get("error") or request.form.get("error") or "delivery failed").strip()
    ok = mark_event_failed(db.session, event_id, error)
    return jsonify({"ok": bool(ok)})


@api_bp.post("/discord/events/heartbeat")
def discord_events_heartbeat():
    if not _discord_secret_ok():
        return jsonify({"ok": False, "message": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    slug = str(data.get("league_slug") or request.args.get("league_slug") or "").strip()
    if not slug:
        slug = str(current_app.config.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        return jsonify({"ok": False, "message": "league_slug is required"}), 400
    bot_name = str(data.get("bot_name") or "").strip()[:120]
    if not bot_name:
        bot_name = "discord-bot"
    row = upsert_bot_heartbeat(
        db.session,
        league_slug=slug,
        bot_name=bot_name,
        bot_version=str(data.get("bot_version") or "").strip()[:64],
        guild_id=str(data.get("guild_id") or "").strip()[:64],
        extra={
            "pending_count": data.get("pending_count"),
            "sent_count": data.get("sent_count"),
            "last_error": str(data.get("last_error") or "").strip()[:400],
        },
    )
    return jsonify(
        {
            "ok": True,
            "heartbeat": {
                "id": int(row.id),
                "league_slug": row.league_slug,
                "bot_name": row.bot_name,
                "last_seen_at": row.last_seen_at.isoformat(timespec="seconds") if row.last_seen_at else None,
            },
        }
    )


@api_bp.post("/news/<int:article_id>/vote")
def news_article_vote(article_id: int):
    slug = str(current_app.config.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        return jsonify({"error": "no_league"}), 400
    if not current_user.is_authenticated:
        return jsonify({"error": "auth"}), 401
    if not viewer_can_react_on_news(current_user, slug):
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    try:
        value = int(payload.get("value", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "bad_value"}), 400
    out = set_article_vote(
        db.session,
        league_slug=slug,
        article_id=article_id,
        user_id=int(current_user.id),
        value=value,
    )
    if out.get("error") == "not_found":
        return jsonify(out), 404
    if out.get("error"):
        return jsonify(out), 400
    return jsonify(out)


@api_bp.post("/news/<int:article_id>/comments")
def news_article_comment_post(article_id: int):
    slug = str(current_app.config.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        return jsonify({"error": "no_league"}), 400
    if not current_user.is_authenticated:
        return jsonify({"error": "auth"}), 401
    if not viewer_can_react_on_news(current_user, slug):
        return jsonify({"error": "forbidden"}), 403
    payload = request.get_json(silent=True) or {}
    body = str(payload.get("body") or "")
    out = add_article_comment(
        db.session,
        league_slug=slug,
        article_id=article_id,
        user_id=int(current_user.id),
        body=body,
    )
    if out.get("error") == "not_found":
        return jsonify(out), 404
    if out.get("error"):
        return jsonify(out), 400
    return jsonify(out)


@api_bp.post("/mobile/push-token")
def mobile_push_token_register():
    """Register or clear a device push token (FCM/APNs) for the current league site user."""
    from datetime import datetime

    slug = str(current_app.config.get("LEAGUE_SLUG") or "").strip()
    if not slug:
        return jsonify({"error": "no_league"}), 400
    if not current_user.is_authenticated:
        return jsonify({"error": "auth"}), 401

    site_engine = db.engines.get("site")
    if site_engine is None:
        return jsonify({"error": "site_db_unavailable"}), 503

    payload = request.get_json(silent=True) or {}
    platform = str(payload.get("platform") or "").strip().lower()
    token = str(payload.get("token") or "").strip()

    if platform not in ("ios", "android"):
        return jsonify({"error": "bad_platform"}), 400

    uid = int(current_user.id)
    now = datetime.utcnow()

    if not token:
        with site_engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM mobile_push_devices "
                    "WHERE user_id = :uid AND league_slug = :slug AND platform = :plat"
                ),
                {"uid": uid, "slug": slug, "plat": platform},
            )
        return jsonify({"ok": True, "cleared": True})

    if len(token) > 4096:
        return jsonify({"error": "bad_token"}), 400

    with site_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO mobile_push_devices (
                    user_id, league_slug, platform, device_token, created_at, updated_at
                ) VALUES (
                    :uid, :slug, :plat, :tok, :now, :now
                )
                ON CONFLICT(user_id, league_slug, platform) DO UPDATE SET
                    device_token = excluded.device_token,
                    updated_at = excluded.updated_at
                """
            ),
            {"uid": uid, "slug": slug, "plat": platform, "tok": token, "now": now},
        )

    return jsonify({"ok": True})
