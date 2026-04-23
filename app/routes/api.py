"""JSON API endpoints for search, lazy box scores, homepage summary."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, url_for

from app.config import Config
from sqlalchemy import func, select, text

from app.logo_urls import team_logo_url_for_team
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
    TeamSeasonAggregate,
    TeamStanding,
    db,
)
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.division_labels import load_division_display_maps
from app.services.homepage_dashboard import (
    build_active_streaks,
    build_around_the_league,
    build_champions_panel,
    build_conf_cutoff_map,
    build_power_rankings,
    build_standings_by_division,
    build_stars_windows,
    build_trending_players,
    league_calendar_anchor_date,
    pick_game_of_the_night,
    pick_next_game_to_watch,
)
from app.services.playoff_bracket import playoff_bracket_payload
from app.services.player_rating_avgs import goalie_category_averages, skater_category_averages
from app.services.player_headshot import resolve_player_headshot_static_filename
from app.services.player_ratings_csv import get_player_ratings_row, player_positions_display_label
from app.services.seasons import get_current_season, season_age_reference_date

api_bp = Blueprint("api", __name__)

_FTS_SAFE = re.compile(r"[^\w\s.-]", re.UNICODE)


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

    Some exports encode OT1 goals with ``period=1``. For OT games that did not reach shootout,
    if all imported periods are <= 3 and event count matches final goals, treat the final goal
    as period 4 (OT) for display.
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


def _recent_form_map(season_id: int) -> dict[int, dict[str, int | str]]:
    games = db.session.scalars(
        select(Game)
        .where(Game.season_id == season_id, Game.status == "final")
        .order_by(Game.game_date.desc().nulls_last(), Game.id.desc())
        .limit(800)
    ).all()
    by_team: dict[int, list[str]] = defaultdict(list)
    for g in games:
        if g.home_team_id and len(by_team[g.home_team_id]) < 10:
            home_res = "W" if (g.home_score or 0) > (g.away_score or 0) else "L"
            by_team[g.home_team_id].append(home_res)
        if g.away_team_id and len(by_team[g.away_team_id]) < 10:
            away_res = "W" if (g.away_score or 0) > (g.home_score or 0) else "L"
            by_team[g.away_team_id].append(away_res)
    out: dict[int, dict[str, int | str]] = {}
    for team_id, recent in by_team.items():
        wins = sum(1 for r in recent if r == "W")
        losses = sum(1 for r in recent if r == "L")
        out[team_id] = {"last10": f"{wins}-{losses}", "last10_wins": wins, "last10_losses": losses}
    return out


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
    return jsonify(
        {
            "id": player.id,
            "name": player.full_name or "",
            "position": player_positions_display_label(player),
            "team_abbr": team.abbreviation if team else "",
            "age": age,
            "shoots": (player.shoots_catches or "").strip(),
            "height_inches": player.height_inches,
            "weight_lbs": player.weight_lbs,
            "abi": float(player.overall_ability) if player.overall_ability is not None else None,
            "pot": float(player.overall_potential) if player.overall_potential is not None else None,
            "is_goalie": is_goalie,
            "attrs": attrs,
            "photo_url": _player_photo_url(player),
        }
    )


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
    season = get_current_season()
    lm = db.session.scalars(
        select(LeagueMeta).where(LeagueMeta.fhm_league_id == 0).limit(1)
    ).first() or db.session.scalars(select(LeagueMeta).limit(1)).first()
    league_info = (
        {"name": lm.name, "abbr": lm.abbreviation or ""} if lm else {"name": "", "abbr": ""}
    )
    if not season:
        empty_news = build_around_the_league()
        return jsonify(
            {
                "league_calendar_date": None,
                "teams": [],
                "standings_by_division": [],
                "game_of_the_night": None,
                "next_game_to_watch": None,
                "stars_last_7d": [],
                "stars_last_14d": [],
                "stars_last_30d": [],
                "trending_players": {"hot": [], "cold": []},
                "active_streaks": {"goal_streak": [], "point_streak": []},
                "power_rankings": {"top5": [], "bottom5": []},
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
                "league": league_info,
                "segment": segment,
            }
        )
    recent_form = _recent_form_map(season.id)
    teams_out: list[dict[str, object]] = []

    fantasy_bowl_leaders_only = (
        str(current_app.config.get("LEAGUE_SLUG") or "") == "bowl-fantasy"
    )
    bowl_fhm_league_ids: tuple[int, ...] | None = None
    if fantasy_bowl_leaders_only:
        bowl_fhm_league_ids = bowl_nhl_league_ids(db.session)
        if not bowl_fhm_league_ids:
            bowl_fhm_league_ids = (0,)

    def leader_rows(stat, order_col, limit=10, goalie=False):
        if goalie:
            q = select(PlayerGoalieStat, Player).join(
                Player, PlayerGoalieStat.player_id == Player.id
            )
            if bowl_fhm_league_ids is not None:
                q = q.join(Team, PlayerGoalieStat.team_id == Team.id).where(
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == segment,
                    Team.fhm_league_id.in_(bowl_fhm_league_ids),
                )
            else:
                q = q.where(
                    PlayerGoalieStat.season_id == season.id,
                    PlayerGoalieStat.stat_segment == segment,
                )
            q = q.order_by(order_col.desc()).limit(limit)
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
                        "team_logo_url": team_logo_url_for_team(tm) if tm else "",
                        "value": getattr(pgs, order_col.key),
                    }
                )
            return out
        q = select(PlayerSkaterStat, Player).join(
            Player, PlayerSkaterStat.player_id == Player.id
        )
        if bowl_fhm_league_ids is not None:
            q = q.join(Team, PlayerSkaterStat.team_id == Team.id).where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.stat_segment == segment,
                Team.fhm_league_id.in_(bowl_fhm_league_ids),
            )
        else:
            q = q.where(
                PlayerSkaterStat.season_id == season.id,
                PlayerSkaterStat.stat_segment == segment,
            )
        q = q.order_by(order_col.desc()).limit(limit)
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
                    "team_logo_url": team_logo_url_for_team(tm) if tm else "",
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
    raw_dir = Path(str(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR)))
    div_pair, div_by_id = load_division_display_maps(raw_dir / "divisions.csv")
    standings_by_division = build_standings_by_division(
        db.session, season.id, div_name_by_pair=div_pair, div_name_by_id=div_by_id
    )
    tm_map = {
        tid: t
        for tid in standings_by_team
        if (t := db.session.get(Team, tid)) is not None
    }
    conf_cutoff = build_conf_cutoff_map(db.session, season.id)
    league_cal = league_calendar_anchor_date(db.session, season.id)
    game_of_the_night = pick_game_of_the_night(
        db.session, season.id, standings_by_team, tm_map, conf_cutoff, None
    )
    next_game_to_watch = pick_next_game_to_watch(
        db.session, season.id, standings_by_team, tm_map, conf_cutoff, league_cal
    )
    stars_bundle = build_stars_windows(db.session, season.id, league_cal)
    trending_players = build_trending_players(db.session, season.id, segment, league_cal)
    active_streaks = build_active_streaks(db.session, season.id)
    agg_rows = db.session.scalars(
        select(TeamSeasonAggregate).where(
            TeamSeasonAggregate.season_id == season.id,
            TeamSeasonAggregate.stat_segment == segment,
        )
    ).all()
    special_teams: list[dict[str, object]] = []
    for row in agg_rows:
        tm = db.session.get(Team, row.team_id)
        if not tm:
            continue
        pp_pct = _pct(row.pp_goals, row.pp_chances)
        pk_pct = None
        if row.sh_chances is not None and row.sh_chances > 0 and row.pk_goals_against is not None:
            pk_pct = (1.0 - (float(row.pk_goals_against) / float(row.sh_chances))) * 100.0
        if pp_pct is None and pk_pct is None:
            continue
        net_st = (pp_pct or 0.0) + (pk_pct or 0.0)
        st = standings_by_team.get(row.team_id)
        special_teams.append(
            {
                "team": tm.abbreviation,
                "team_name": tm.full_display_name(),
                "team_city": (tm.city or tm.name or "").strip(),
                "team_slug": tm.slug,
                "team_logo_url": team_logo_url_for_team(tm),
                "pp_pct": round(pp_pct, 1) if pp_pct is not None else None,
                "pk_pct": round(pk_pct, 1) if pk_pct is not None else None,
                "net_st": round(net_st, 1),
                "hits": row.hits,
                "blocks": row.blocked_shots,
                "fo_pct": round(row.faceoff_pct, 1) if row.faceoff_pct is not None else None,
                "gp": st.standing_gp_display() if st else None,
            }
        )
    special_teams.sort(key=lambda x: float(x.get("net_st") or 0), reverse=True)

    power_rankings = build_power_rankings(
        db.session, season.id, standings_by_team, special_teams, recent_form, segment
    )
    champions_panel = build_champions_panel(db.session)
    around_the_league = build_around_the_league()

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
                "home_logo_url": team_logo_url_for_team(ht) if ht else "",
                "away_logo_url": team_logo_url_for_team(at) if at else "",
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
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
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
                "team_logo_url": team_logo_url_for_team(tm) if tm else "",
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

    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
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
                    "team_logo_url": team_logo_url_for_team(tm) if tm else "",
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
                "home_logo_url": team_logo_url_for_team(ht) if ht else "",
                "away_logo_url": team_logo_url_for_team(at) if at else "",
                "home_slug": ht.slug if ht else "",
                "away_slug": at.slug if at else "",
            }
        )

    return jsonify(
        {
            "league_calendar_date": league_cal.isoformat(),
            "teams": teams_out,
            "standings_by_division": standings_by_division,
            "game_of_the_night": game_of_the_night,
            "next_game_to_watch": next_game_to_watch,
            "stars_last_7d": stars_bundle.get("stars_last_7d", []),
            "stars_last_14d": stars_bundle.get("stars_last_14d", []),
            "stars_last_30d": stars_bundle.get("stars_last_30d", []),
            "trending_players": trending_players,
            "active_streaks": active_streaks,
            "power_rankings": power_rankings,
            "champions_panel": champions_panel,
            "around_the_league": around_the_league,
            "leaders": leaders,
            "games": games_out,
            "upcoming": upcoming_out,
            "special_teams": special_teams,
            "rookies": rookies,
            "league_spotlight": league_spotlight,
            "identity_panel": identity_panel,
            "league": league_info,
            "segment": segment,
        }
    )


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
