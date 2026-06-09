from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models import Game, Season, Team, TeamSeasonAggregate, TeamStanding, db
from app.services.advanced_stats import _game_points_for_team
from app.services.playoff_bracket import is_playoff_game_type

RS_GAME_CAP = 82


def _conference_id_map(rows: list[TeamStanding]) -> dict[str, int]:
    """Infer East/West mapping from available team conference ids when names are missing."""
    ids = sorted(
        {
            int(r.team.fhm_conference_id)
            for r in rows
            if r.team is not None and r.team.fhm_conference_id is not None
        }
    )
    if len(ids) < 2:
        return {}
    return {
        "east": ids[0],
        "west": ids[-1],
        # Classic NHL naming aliases used by Fantasy data.
        "wales": ids[0],
        "campbell": ids[-1],
    }


def standings_for_season(season: Season | None, conference: str | None = None, division: str | None = None):
    if not season:
        return []
    q = (
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == season.id)
        .order_by(TeamStanding.pts.desc(), (TeamStanding.gf - TeamStanding.ga).desc())
    )
    rows = db.session.scalars(q).all()
    if conference:
        conf_key = conference.strip().lower()
        by_name = [r for r in rows if (r.conference or "").strip().lower() == conf_key]
        if by_name:
            rows = by_name
        else:
            # Fallback: some imports leave TeamStanding.conference NULL but do set team fhm_conference_id.
            id_map = _conference_id_map(rows)
            target_id = id_map.get(conf_key)
            if target_id is not None:
                rows = [
                    r
                    for r in rows
                    if r.team is not None and r.team.fhm_conference_id == target_id
                ]
            else:
                rows = []
    if division:
        rows = [r for r in rows if (r.division or "") == division]
    return rows


def conferences_for_season(season: Season | None) -> list[str]:
    if not season:
        return []
    q = (
        select(TeamStanding)
        .options(joinedload(TeamStanding.team))
        .where(TeamStanding.season_id == season.id)
    )
    rows = db.session.scalars(q).all()
    names = sorted({(r.conference or "").strip() for r in rows if (r.conference or "").strip()})
    if names:
        return names
    # Fallback to East/West when conference text is missing but conference ids exist.
    id_map = _conference_id_map(rows)
    out: list[str] = []
    if "east" in id_map:
        out.append("East")
    if "west" in id_map:
        out.append("West")
    return out


def divisions_for_season(season: Season | None) -> list[str]:
    if not season:
        return []
    q = select(TeamStanding.division).where(TeamStanding.season_id == season.id).distinct()
    return sorted({d for d in db.session.scalars(q).all() if d})


def _game_counts_for_stat_segment(game: Game, stat_segment: str) -> bool:
    """True if this final game should count toward the given aggregate segment (rs vs playoffs)."""
    po = is_playoff_game_type(game.game_type or "")
    if stat_segment == "rs":
        return not po
    if stat_segment in ("po", "ps"):
        return po
    return not po


def _home_games_count_by_team(season_id: int, stat_segment: str) -> dict[int, int]:
    games = db.session.scalars(
        select(Game).where(Game.season_id == season_id, Game.status == "final")
    ).all()
    counts: dict[int, int] = {}
    for g in games:
        if not _game_counts_for_stat_segment(g, stat_segment):
            continue
        tid = int(g.home_team_id)
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def _max_home_attendance_by_team(season_id: int, stat_segment: str) -> dict[int, int]:
    """Largest reported home attendance in the segment (proxy for listed arena capacity)."""
    games = db.session.scalars(
        select(Game).where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.attendance.isnot(None),
        )
    ).all()
    mx: dict[int, int] = {}
    for g in games:
        if not _game_counts_for_stat_segment(g, stat_segment):
            continue
        att = g.attendance
        if att is None:
            continue
        tid = int(g.home_team_id)
        iv = int(att)
        prev = mx.get(tid, 0)
        if iv > prev:
            mx[tid] = iv
    return mx


def _mean_home_capacity_pct_per_game(
    season_id: int, stat_segment: str, capacity_by_team: dict[int, int]
) -> dict[int, float]:
    """Mean of (100 × attendance ÷ arena capacity) over home games with reported attendance."""
    games = db.session.scalars(
        select(Game).where(Game.season_id == season_id, Game.status == "final")
    ).all()
    sums: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    for g in games:
        if not _game_counts_for_stat_segment(g, stat_segment):
            continue
        tid = int(g.home_team_id)
        att = g.attendance
        if att is None:
            continue
        cap = capacity_by_team.get(tid)
        if not cap or cap <= 0:
            continue
        sums[tid] += 100.0 * float(att) / float(cap)
        counts[tid] += 1
    return {tid: sums[tid] / counts[tid] for tid in sums if counts[tid] > 0}


def _effective_home_gp(from_games: int | None, standing: TeamStanding | None) -> int | None:
    """Home games for averages; cap inflated schedule counts at ~½ of standings GP."""
    gp_total = int(standing.standing_gp_display()) if standing is not None else 0
    half = max(1, gp_total // 2) if gp_total > 0 else None
    if from_games and from_games > 0:
        if half is not None and from_games > half + 2:
            return half
        return from_games
    if half is not None:
        return half
    return None


def _resolve_cap_pct_mean(
    team_id: int,
    agg: TeamSeasonAggregate | None,
    home_gp_eff: int | None,
    capacity_by_team: dict[int, int],
    mean_from_games: dict[int, float],
) -> float | None:
    """Prefer per-game average capacity %; else total-based; else FHM ``capacity_use_pct``."""
    if team_id in mean_from_games:
        return mean_from_games[team_id]
    cap = capacity_by_team.get(team_id)
    if (
        agg
        and agg.attendance_home is not None
        and home_gp_eff
        and home_gp_eff > 0
        and cap
        and cap > 0
    ):
        return 100.0 * float(agg.attendance_home) / float(home_gp_eff) / float(cap)
    if agg and agg.capacity_use_pct is not None:
        v = float(agg.capacity_use_pct)
        if 0 < v <= 1.0:
            return v * 100.0
        return v
    return None


def team_aggregate_rows(
    season: Season | None,
    standings_rows: list,
    stat_segment: str,
) -> list[tuple[Team, TeamSeasonAggregate | None, int | None, float | None]]:
    """Pair teams with aggregates, effective home GP, and mean CAP % (capacity use per home game).

    CAP % is the **average** of ``100 × attendance ÷ arena_capacity`` over home games in the
    segment (capacity = max single-game home attendance for that team). Falls back to the
    season total formula or FHM ``capacity_use_pct`` when game-level data is missing.
    """
    if not season:
        return []
    home_gp_raw = _home_games_count_by_team(season.id, stat_segment)
    capacity_by_team = _max_home_attendance_by_team(season.id, stat_segment)
    mean_cap_pct = _mean_home_capacity_pct_per_game(
        season.id, stat_segment, capacity_by_team
    )
    q = (
        select(TeamSeasonAggregate)
        .options(joinedload(TeamSeasonAggregate.team))
        .where(
            TeamSeasonAggregate.season_id == season.id,
            TeamSeasonAggregate.stat_segment == stat_segment,
        )
    )
    agg_rows = db.session.scalars(q).all()
    by_team_id = {a.team_id: a for a in agg_rows}
    if standings_rows:
        out: list[tuple[Team, TeamSeasonAggregate | None, int | None, float | None]] = []
        for st in standings_rows:
            tid = int(st.team_id)
            agg = by_team_id.get(st.team_id)
            hgp = _effective_home_gp(home_gp_raw.get(tid), st)
            cap_pct = _resolve_cap_pct_mean(
                tid, agg, hgp, capacity_by_team, mean_cap_pct
            )
            out.append((st.team, agg, hgp, cap_pct))
        return out
    out2: list[tuple[Team, TeamSeasonAggregate | None, int | None, float | None]] = []
    for a in sorted(
        agg_rows,
        key=lambda x: (x.team.name.lower() if x.team else "", x.team_id),
    ):
        if a.team:
            tid = int(a.team_id)
            hgp = _effective_home_gp(home_gp_raw.get(tid), None)
            cap_pct = _resolve_cap_pct_mean(
                tid, a, hgp, capacity_by_team, mean_cap_pct
            )
            out2.append((a.team, a, hgp, cap_pct))
    return out2


def _standings_trend_gp_limit(standing: TeamStanding | None) -> int:
    if standing is None:
        return RS_GAME_CAP
    gp = int(standing.standing_gp_display() or standing.gp or 0)
    if gp <= 0:
        return RS_GAME_CAP
    return min(gp, RS_GAME_CAP)


def _game_result_lines(game: Game, team_id: int, opponent_abbr: str) -> tuple[str, str | None]:
    home_score = game.home_score
    away_score = game.away_score
    opp = (opponent_abbr or "OPP").strip() or "OPP"
    if home_score is None or away_score is None:
        return (f"Game vs {opp}", None)
    is_home = int(game.home_team_id) == int(team_id)
    team_score = int(home_score if is_home else away_score)
    opp_score = int(away_score if is_home else home_score)
    if team_score > opp_score:
        if game.went_to_shootout:
            return (f"Win vs {opp}", "in shootout")
        if game.went_to_overtime:
            return (f"Win vs {opp}", "in overtime")
        return (f"Win vs {opp}", None)
    if team_score == opp_score:
        if game.went_to_shootout:
            return (f"Tie vs {opp}", "in shootout")
        if game.went_to_overtime:
            return (f"Tie vs {opp}", "in overtime")
        return (f"Tie vs {opp}", None)
    if game.went_to_shootout:
        return (f"Loss vs {opp}", "in shootout")
    if game.went_to_overtime:
        return (f"Loss vs {opp}", "in overtime")
    return (f"Loss vs {opp}", "in regulation")


def _standings_trend_title(
    *,
    view: str,
    league_display_name: str,
    sel_conference: str | None,
    sel_division: str | None,
) -> str:
    if view == "conference":
        if sel_conference:
            return sel_conference
        return "Conference"
    if view == "division":
        if sel_division:
            return sel_division
        return "Division"
    return league_display_name or "League"


def build_standings_trend_chart(
    session,
    season_id: int | None,
    standings_rows: list[TeamStanding],
    *,
    view: str = "overall",
    sel_conference: str | None = None,
    sel_division: str | None = None,
    logo_season_year: int | None = None,
    league_display_name: str = "League",
) -> dict[str, Any]:
    """MoneyPuck-style regular-season standings trend payload for the standings page."""
    empty: dict[str, Any] = {
        "title": _standings_trend_title(
            view=view,
            league_display_name=league_display_name,
            sel_conference=sel_conference,
            sel_division=sel_division,
        ),
        "y_axis_label": "Points Above Group Average",
        "x_axis_label": "Games Played",
        "rs_game_cap": RS_GAME_CAP,
        "max_gp": 0,
        "teams": [],
    }
    if not season_id or not standings_rows:
        return empty

    team_by_id: dict[int, Team] = {}
    standing_by_id: dict[int, TeamStanding] = {}
    for st in standings_rows:
        if st.team is None:
            continue
        tid = int(st.team_id)
        team_by_id[tid] = st.team
        standing_by_id[tid] = st
    team_ids = set(team_by_id.keys())
    if not team_ids:
        return empty

    games = session.scalars(
        select(Game)
        .where(
            Game.season_id == season_id,
            Game.status == "final",
            Game.home_score.is_not(None),
            Game.away_score.is_not(None),
        )
        .order_by(Game.game_date.asc().nulls_last(), Game.id.asc())
    ).all()

    team_games: dict[int, list[Game]] = defaultdict(list)
    for game in games:
        if is_playoff_game_type(game.game_type):
            continue
        for tid in (int(game.home_team_id), int(game.away_team_id)):
            if tid in team_ids:
                team_games[tid].append(game)

    raw_series: dict[int, list[dict[str, Any]]] = {}
    max_gp = 0
    for tid in team_ids:
        gp_limit = _standings_trend_gp_limit(standing_by_id.get(tid))
        points_total = 0
        series: list[dict[str, Any]] = []
        for game in team_games.get(tid, []):
            is_home = int(game.home_team_id) == tid
            opp_id = int(game.away_team_id if is_home else game.home_team_id)
            opp = team_by_id.get(opp_id)
            opp_abbr = (opp.abbreviation or opp.name or "OPP") if opp else "OPP"
            gp = len(series) + 1
            if gp > gp_limit:
                break
            points_total += _game_points_for_team(game, tid)
            line1, line2 = _game_result_lines(game, tid, opp_abbr)
            series.append(
                {
                    "gp": gp,
                    "points_total": points_total,
                    "result_line1": line1,
                    "result_line2": line2,
                    "opponent_abbr": opp_abbr,
                    "is_home": is_home,
                }
            )
        if series:
            raw_series[tid] = series
            last_gp = int(series[-1]["gp"])
            if last_gp > max_gp:
                max_gp = last_gp

    if not raw_series:
        return empty

    max_gp = min(max_gp, RS_GAME_CAP)
    points_by_gp: dict[int, list[float]] = defaultdict(list)
    for series in raw_series.values():
        for pt in series:
            points_by_gp[int(pt["gp"])].append(float(pt["points_total"]))

    avg_by_gp: dict[int, float] = {}
    for gp, vals in points_by_gp.items():
        avg_by_gp[gp] = sum(vals) / len(vals) if vals else 0.0

    from app.services.season_team_logo_bundle import get_season_team_logo_bundle

    logo_bundle = get_season_team_logo_bundle()
    sy = logo_season_year

    teams_out: list[dict[str, Any]] = []
    for tid in sorted(
        raw_series.keys(),
        key=lambda t: (
            -float(raw_series[t][-1]["points_total"]) if raw_series[t] else 0,
            str(team_by_id[t].abbreviation or team_by_id[t].name or ""),
        ),
    ):
        tm = team_by_id[tid]
        color = (tm.primary_color or tm.secondary_color or "#60a5fa").strip() or "#60a5fa"
        plotted: list[dict[str, Any]] = []
        for pt in raw_series[tid]:
            gp = int(pt["gp"])
            if gp > max_gp:
                continue
            avg_pts = avg_by_gp.get(gp, float(pt["points_total"]))
            value = float(pt["points_total"]) - avg_pts
            plotted.append(
                {
                    "gp": gp,
                    "value": round(value, 2),
                    "points_total": int(pt["points_total"]),
                    "result_line1": pt["result_line1"],
                    "result_line2": pt["result_line2"],
                    "opponent_abbr": pt["opponent_abbr"],
                    "is_home": pt["is_home"],
                }
            )
        if not plotted:
            continue
        teams_out.append(
            {
                "team_id": tid,
                "name": tm.full_display_name(),
                "abbr": tm.abbreviation or tm.name or "",
                "slug": tm.slug,
                "color": color,
                "logo_url": logo_bundle.team_logo_url_for_season_context(tm, sy),
                "points": plotted,
                "final_value": plotted[-1]["value"],
                "max_gp": plotted[-1]["gp"],
            }
        )

    return {
        "title": _standings_trend_title(
            view=view,
            league_display_name=league_display_name,
            sel_conference=sel_conference,
            sel_division=sel_division,
        ),
        "y_axis_label": "Points Above Group Average",
        "x_axis_label": "Games Played",
        "rs_game_cap": RS_GAME_CAP,
        "max_gp": max_gp,
        "teams": teams_out,
    }
