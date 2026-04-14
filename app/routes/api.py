"""JSON API endpoints for search, lazy box scores, homepage summary."""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request, url_for
from sqlalchemy import select, text

from app.logo_urls import team_logo_url_for_team
from app.models import (
    Game,
    GameGoalieStat,
    GameSkaterStat,
    LeagueMeta,
    Player,
    PlayerGoalieStat,
    PlayerSkaterStat,
    ScoringEvent,
    Team,
    db,
)
from app.services.all_time_records import bowl_nhl_league_ids
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
        return jsonify(
            {
                "teams": [],
                "leaders": {
                    "goals": [],
                    "assists": [],
                    "points": [],
                    "goalie_wins": [],
                    "goalie_shutouts": [],
                },
                "games": [],
                "league": league_info,
                "segment": segment,
            }
        )
    from app.models import TeamStanding
    from sqlalchemy.orm import joinedload

    standings = (
        db.session.scalars(
            select(TeamStanding)
            .options(joinedload(TeamStanding.team))
            .where(TeamStanding.season_id == season.id)
            .order_by(TeamStanding.pts.desc())
            .limit(8)
        )
        .all()
    )
    teams_out = []
    for i, st in enumerate(standings, start=1):
        tm = st.team
        teams_out.append(
            {
                "rank": i,
                "slug": tm.slug,
                "name": tm.name,
                "abbr": tm.abbreviation,
                "logo_url": team_logo_url_for_team(tm),
                "gp": st.gp,
                "w": st.w,
                "l": st.l,
                "t": st.ties,
                "otl": st.otl,
                "sow": st.shootout_wins,
                "sol": st.shootout_losses,
                "pts": st.pts,
            }
        )

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
            "teams": teams_out,
            "leaders": leaders,
            "games": games_out,
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
