"""Franchise history leader boards: RS and playoffs from season stats + BOWL/NHL career CSVs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import extract, func, or_, select

from app.models import (
    Game,
    GameGoalieStat,
    GameSkaterStat,
    Player,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    Season,
    Team,
    db,
)
from app.services.all_time_records import bowl_nhl_league_ids

# Career CSV sources aligned with all_time_records (rs vs po splits).
FRANCHISE_CAREER_RS_SKATER: tuple[str, ...] = ("rs", "retired_rs")
FRANCHISE_CAREER_RS_GOALIE: tuple[str, ...] = ("rs", "retired_rs")
FRANCHISE_CAREER_PO_SKATER: tuple[str, ...] = ("po", "retired_po")
FRANCHISE_CAREER_PO_GOALIE: tuple[str, ...] = ("po", "retired_po")
TOP_N = 6
# Wider Season rows are not used alone for overlap checks (FHM often has one Season spanning
# the whole sim); boxscore-derived spans are preferred. Narrow spans still match career years.
_MAX_SEASON_ROW_FALLBACK_SPAN_YEARS = 3


def _team_fhm_int(team: Team) -> int | None:
    raw = team.fhm_team_id
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


def _team_career_clause(team: Team, team_fhm_col, team_id_col):
    """Match career CSV rows to this franchise (FK and/or FHM team id)."""
    parts = []
    if team.id is not None:
        parts.append(team_id_col == team.id)
    fhm = _team_fhm_int(team)
    if fhm is not None:
        parts.append(team_fhm_col == fhm)
    if not parts:
        return None
    return or_(*parts) if len(parts) > 1 else parts[0]


def _season_inclusive_year_span(season: Season | None) -> tuple[int, int] | None:
    """Calendar span for overlap with career CSV `season_year`; None if unknown."""
    if season is None:
        return None
    y0, y1 = season.start_year, season.end_year
    if y0 is None and y1 is None:
        return None
    if y0 is None:
        y0 = y1
    if y1 is None:
        y1 = y0
    assert y0 is not None and y1 is not None
    lo, hi = (y0, y1) if y0 <= y1 else (y1, y0)
    return (lo, hi)


def _franchise_career_years_by_player(
    team: Team,
    career_sources: tuple[str, ...],
    line_model: type,
    player_ids: list[int],
) -> dict[int, set[int]]:
    """season_year values present in career imports for this franchise (BOWL/NHL)."""
    if not player_ids:
        return {}
    tc = _team_career_clause(team, line_model.team_fhm_id, line_model.team_id)
    if tc is None:
        return {}
    league_ids = bowl_nhl_league_ids(db.session)
    if not league_ids:
        league_ids = (0,)
    q = select(line_model.player_id, line_model.season_year).where(
        tc,
        line_model.league_fhm_id.in_(league_ids),
        line_model.career_source.in_(career_sources),
        line_model.player_id.in_(player_ids),
    )
    out: dict[int, set[int]] = {}
    for pid, yr in db.session.execute(q).all():
        if yr is None:
            continue
        out.setdefault(int(pid), set()).add(int(yr))
    return out


def _span_overlaps_career_years(span: tuple[int, int], years: set[int]) -> bool:
    lo, hi = span
    return any(lo <= y <= hi for y in years)


def _player_team_season_game_year_bounds(
    team_id: int,
    player_ids: list[int],
    season_ids: list[int],
    line_model: type[GameSkaterStat] | type[GameGoalieStat],
) -> dict[tuple[int, int], tuple[int, int]]:
    """Min/max calendar years from games this player played for this team in each season."""
    if not player_ids or not season_ids:
        return {}
    q = (
        select(
            line_model.player_id,
            Game.season_id,
            func.min(extract("year", Game.game_date)).label("y0"),
            func.max(extract("year", Game.game_date)).label("y1"),
        )
        .join(Game, line_model.game_id == Game.id)
        .where(
            line_model.team_id == team_id,
            line_model.player_id.in_(player_ids),
            Game.season_id.in_(season_ids),
            Game.game_date.isnot(None),
        )
        .group_by(line_model.player_id, Game.season_id)
    )
    out: dict[tuple[int, int], tuple[int, int]] = {}
    for pid, sid, y0, y1 in db.session.execute(q).all():
        if y0 is None or y1 is None:
            continue
        a, b = int(y0), int(y1)
        lo, hi = (a, b) if a <= b else (b, a)
        out[(int(pid), int(sid))] = (lo, hi)
    return out


def _should_skip_season_stat_for_franchise_career(
    *,
    pid: int,
    season_id: int,
    season: Season,
    yset: set[int],
    pl: Player | None,
    game_bounds: dict[tuple[int, int], tuple[int, int]],
) -> bool:
    """True when this season stat row is already represented in franchise career imports."""
    if not yset:
        return False
    span = game_bounds.get((pid, season_id))
    if span is not None:
        return _span_overlaps_career_years(span, yset)
    table_span = _season_inclusive_year_span(season)
    if table_span is not None:
        lo, hi = table_span
        if hi - lo <= _MAX_SEASON_ROW_FALLBACK_SPAN_YEARS:
            return _span_overlaps_career_years(table_span, yset)
        if pl is not None and pl.retired:
            return True
    return False


def _is_goalie(pl: Player | None) -> bool:
    """True when roster lists the player as G (skater franchise boards exclude them)."""
    return (pl.position or "").strip().upper() == "G"


def _fmt_plus_minus(val: float | int | None) -> str:
    if val is None:
        return "—"
    iv = int(val)
    if iv > 0:
        return f"+{iv}"
    return str(iv)


def _fmt_sv_pct(val: float | int | None) -> str:
    if val is None:
        return "—"
    s = f"{float(val):.3f}"
    return s[1:] if s.startswith("0") else s


def _goalie_gaa_from_totals(ga: int, gp: int, minutes_played: float) -> float | None:
    """Compute GAA robustly across imports with inconsistent minute semantics.

    Primary formula is ``GA * 60 / minutes_played`` when minutes look plausible
    for real game time. Some imports store minute-like values that are much lower
    than true elapsed game minutes; in those cases we fall back to ``GA / GP``.
    """
    if gp <= 0:
        return None
    if minutes_played > 0 and minutes_played >= (float(gp) * 45.0):
        return (float(ga) * 60.0) / float(minutes_played)
    return float(ga) / float(gp)


def _top_players(
    aggs: list[dict[str, object]],
    value_fn: Callable[[dict[str, object]], float | int | None],
    *,
    higher_better: bool,
    limit: int = TOP_N,
) -> list[tuple[dict[str, object], float]]:
    scored: list[tuple[dict[str, object], float]] = []
    for a in aggs:
        raw = value_fn(a)
        if raw is None:
            continue
        if isinstance(raw, float) and raw != raw:  # NaN
            continue
        fv = float(raw)
        scored.append((a, fv))
    if higher_better:
        scored.sort(key=lambda x: (-x[1], x[0]["player"].id))
    else:
        scored.sort(key=lambda x: (x[1], x[0]["player"].id))
    return scored[:limit]


@dataclass(frozen=True)
class _LeaderCategory:
    title: str
    higher_better: bool
    value_fn: Callable[[dict[str, object]], float | int | None]
    format_fn: Callable[[float | int | None], str]


def _skater_categories() -> tuple[_LeaderCategory, ...]:
    return (
        _LeaderCategory("GAMES PLAYED", True, lambda a: a["gp"], lambda v: str(int(v or 0))),
        _LeaderCategory("GOALS", True, lambda a: a["goals"], lambda v: str(int(v or 0))),
        _LeaderCategory("ASSISTS", True, lambda a: a["assists"], lambda v: str(int(v or 0))),
        _LeaderCategory("POINTS", True, lambda a: a["points"], lambda v: str(int(v or 0))),
        _LeaderCategory("POWERPLAY GOALS", True, lambda a: a["ppg"], lambda v: str(int(v or 0))),
        _LeaderCategory("POWERPLAY ASSISTS", True, lambda a: a["ppa"], lambda v: str(int(v or 0))),
        _LeaderCategory("SHORTHANDED GOALS", True, lambda a: a["shg"], lambda v: str(int(v or 0))),
        _LeaderCategory("SHORTHANDED ASSISTS", True, lambda a: a["sha"], lambda v: str(int(v or 0))),
        _LeaderCategory("GAME-WINNING GOALS", True, lambda a: a["gwg"], lambda v: str(int(v or 0))),
        _LeaderCategory("+/-", True, lambda a: a["plus_minus"], _fmt_plus_minus),
        _LeaderCategory("PIM", True, lambda a: a["pim"], lambda v: str(int(v or 0))),
        _LeaderCategory("HITS", True, lambda a: a["hits"], lambda v: str(int(v or 0))),
        _LeaderCategory("FIGHTS", True, lambda a: a["fights"], lambda v: str(int(v or 0))),
        _LeaderCategory("SHOTS BLOCKED", True, lambda a: a["blocked_shots"], lambda v: str(int(v or 0))),
        _LeaderCategory("SOG", True, lambda a: a["shots"], lambda v: str(int(v or 0))),
    )


def _goalie_categories() -> tuple[_LeaderCategory, ...]:
    return (
        _LeaderCategory("GAMES PLAYED", True, lambda a: a["gp"], lambda v: str(int(v or 0))),
        _LeaderCategory("GAMES STARTED", True, lambda a: a["gs"], lambda v: str(int(v or 0))),
        _LeaderCategory("WINS", True, lambda a: a["wins"], lambda v: str(int(v or 0))),
        _LeaderCategory("LOSSES", True, lambda a: a["losses"], lambda v: str(int(v or 0))),
        _LeaderCategory("OTL", True, lambda a: a["otl"], lambda v: str(int(v or 0))),
        _LeaderCategory("SHOTS AGAINST", True, lambda a: a["sa"], lambda v: str(int(v or 0))),
        _LeaderCategory("SHUTOUTS", True, lambda a: a["so"], lambda v: str(int(v or 0))),
        _LeaderCategory("GAA", False, lambda a: a["gaa"], lambda v: f"{float(v):.2f}" if v is not None else "—"),
        _LeaderCategory("SV%", True, lambda a: a["sv_pct"], _fmt_sv_pct),
    )


def _load_skater_aggregates(team: Team, stat_segment: str) -> list[dict[str, object]]:
    """Season skater totals for this franchise, omitting rows already covered by career CSVs.

    When a season row's calendar span intersects a career line's ``season_year`` for the
    same player on this franchise, the career import is treated as canonical (matches
    all-time records) so we do not add season stats on top (avoids double-counting).
    """
    career_sources = (
        FRANCHISE_CAREER_RS_SKATER
        if stat_segment == "rs"
        else FRANCHISE_CAREER_PO_SKATER
    )
    q = (
        select(PlayerSkaterStat, Season)
        .join(Season, PlayerSkaterStat.season_id == Season.id)
        .where(
            PlayerSkaterStat.team_id == team.id,
            PlayerSkaterStat.stat_segment == stat_segment,
        )
    )
    raw_rows = db.session.execute(q).all()
    if not raw_rows:
        return []

    pids = list({int(st.player_id) for st, _ in raw_rows})
    sids = list({int(st.season_id) for st, _ in raw_rows})
    career_years = _franchise_career_years_by_player(
        team, career_sources, PlayerSkaterCareerLine, pids
    )
    game_bounds = _player_team_season_game_year_bounds(
        team.id, pids, sids, GameSkaterStat
    )
    players_by_id = {
        p.id: p
        for p in db.session.scalars(select(Player).where(Player.id.in_(pids))).all()
    }

    acc: dict[int, dict[str, object]] = {}
    for stat, season in raw_rows:
        pid = int(stat.player_id)
        sid = int(stat.season_id)
        yset = career_years.get(pid) or set()
        if _should_skip_season_stat_for_franchise_career(
            pid=pid,
            season_id=sid,
            season=season,
            yset=yset,
            pl=players_by_id.get(pid),
            game_bounds=game_bounds,
        ):
            continue
        if pid not in acc:
            acc[pid] = {
                "gp": 0,
                "goals": 0,
                "assists": 0,
                "points": 0,
                "ppg": 0,
                "ppa": 0,
                "shg": 0,
                "sha": 0,
                "gwg": 0,
                "plus_minus": 0,
                "pim": 0,
                "hits": 0,
                "fights": 0,
                "blocked_shots": 0,
                "shots": 0,
                "_pm_defined": False,
            }
        m = acc[pid]
        m["gp"] = int(m["gp"]) + int(stat.gp or 0)
        m["goals"] = int(m["goals"]) + int(stat.goals or 0)
        m["assists"] = int(m["assists"]) + int(stat.assists or 0)
        g, a = int(stat.goals or 0), int(stat.assists or 0)
        row_pts = stat.points
        m["points"] = int(m["points"]) + (
            int(row_pts) if row_pts is not None else g + a
        )
        m["ppg"] = int(m["ppg"]) + int(stat.ppg or 0)
        m["ppa"] = int(m["ppa"]) + int(stat.pp_assists or 0)
        m["shg"] = int(m["shg"]) + int(stat.shg or 0)
        m["sha"] = int(m["sha"]) + int(stat.sh_assists or 0)
        m["gwg"] = int(m["gwg"]) + int(stat.gwg or 0)
        if stat.plus_minus is not None:
            m["plus_minus"] = int(m["plus_minus"]) + int(stat.plus_minus)
            m["_pm_defined"] = True
        m["pim"] = int(m["pim"]) + int(stat.pim or 0)
        m["hits"] = int(m["hits"]) + int(stat.hits or 0)
        m["fights"] = int(m["fights"]) + int(stat.fights or 0)
        m["blocked_shots"] = int(m["blocked_shots"]) + int(stat.blocked_shots or 0)
        m["shots"] = int(m["shots"]) + int(stat.shots or 0)

    if not acc:
        return []
    players = {
        p.id: p
        for p in db.session.scalars(select(Player).where(Player.id.in_(list(acc)))).all()
    }
    out: list[dict[str, object]] = []
    for pid, m in acc.items():
        pl = players.get(pid)
        if pl is None or _is_goalie(pl):
            continue
        gp = int(m["gp"] or 0)
        if gp <= 0:
            continue
        pm_i: int | None
        if m.get("_pm_defined"):
            pm_i = int(m["plus_minus"])
        else:
            pm_i = None
        goals = int(m["goals"] or 0)
        assists = int(m["assists"] or 0)
        out.append(
            {
                "player": pl,
                "gp": gp,
                "goals": goals,
                "assists": assists,
                "points": int(m["points"] or 0),
                "ppg": int(m["ppg"] or 0),
                "ppa": int(m["ppa"] or 0),
                "shg": int(m["shg"] or 0),
                "sha": int(m["sha"] or 0),
                "gwg": int(m["gwg"] or 0),
                "plus_minus": pm_i,
                "pim": int(m["pim"] or 0),
                "hits": int(m["hits"] or 0),
                "fights": int(m["fights"] or 0),
                "blocked_shots": int(m["blocked_shots"] or 0),
                "shots": int(m["shots"] or 0),
            }
        )
    return out


def _load_skater_career_franchise(
    team: Team, career_sources: tuple[str, ...]
) -> list[dict[str, object]]:
    """Career lines for this franchise (BOWL/NHL leagues), sources = RS or PO export names."""
    tc = _team_career_clause(team, PlayerSkaterCareerLine.team_fhm_id, PlayerSkaterCareerLine.team_id)
    if tc is None:
        return []
    league_ids = bowl_nhl_league_ids(db.session)
    if not league_ids:
        league_ids = (0,)
    line = PlayerSkaterCareerLine
    q = (
        select(
            line.player_id.label("pid"),
            func.coalesce(func.sum(line.gp), 0).label("gp"),
            func.coalesce(func.sum(line.goals), 0).label("goals"),
            func.coalesce(func.sum(line.assists), 0).label("assists"),
            func.coalesce(func.sum(func.coalesce(line.pp_goals, 0)), 0).label("ppg"),
            func.coalesce(func.sum(func.coalesce(line.pp_assists, 0)), 0).label("ppa"),
            func.coalesce(func.sum(func.coalesce(line.sh_goals, 0)), 0).label("shg"),
            func.coalesce(func.sum(func.coalesce(line.sh_assists, 0)), 0).label("sha"),
            func.coalesce(func.sum(func.coalesce(line.gwg, 0)), 0).label("gwg"),
            func.coalesce(func.sum(func.coalesce(line.plus_minus, 0)), 0).label("plus_minus"),
            func.coalesce(func.sum(line.pim), 0).label("pim"),
            func.coalesce(func.sum(func.coalesce(line.hits, 0)), 0).label("hits"),
            func.coalesce(func.sum(func.coalesce(line.fights, 0)), 0).label("fights"),
            func.coalesce(func.sum(func.coalesce(line.sb, 0)), 0).label("blocked_shots"),
            func.coalesce(func.sum(func.coalesce(line.shots, 0)), 0).label("shots"),
        )
        .where(
            tc,
            line.league_fhm_id.in_(league_ids),
            line.career_source.in_(career_sources),
        )
        .group_by(line.player_id)
    )
    rows = db.session.execute(q).all()
    if not rows:
        return []
    pids = [int(r.pid) for r in rows]
    players = {
        p.id: p
        for p in db.session.scalars(select(Player).where(Player.id.in_(pids))).all()
    }
    out: list[dict[str, object]] = []
    for r in rows:
        pl = players.get(int(r.pid))
        if pl is None or _is_goalie(pl):
            continue
        gp = int(r.gp or 0)
        if gp <= 0:
            continue
        goals = int(r.goals or 0)
        assists = int(r.assists or 0)
        out.append(
            {
                "player": pl,
                "gp": gp,
                "goals": goals,
                "assists": assists,
                "points": goals + assists,
                "ppg": int(r.ppg or 0),
                "ppa": int(r.ppa or 0),
                "shg": int(r.shg or 0),
                "sha": int(r.sha or 0),
                "gwg": int(r.gwg or 0),
                "plus_minus": int(r.plus_minus or 0),
                "pim": int(r.pim or 0),
                "hits": int(r.hits or 0),
                "fights": int(r.fights or 0),
                "blocked_shots": int(r.blocked_shots or 0),
                "shots": int(r.shots or 0),
            }
        )
    return out


def _merge_skater_franchise_aggs(
    season: list[dict[str, object]],
    career: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Sum per-player season DB totals (deduped vs career) and career CSV franchise totals."""
    keys = (
        "gp",
        "goals",
        "assists",
        "ppg",
        "ppa",
        "shg",
        "sha",
        "gwg",
        "pim",
        "hits",
        "fights",
        "blocked_shots",
        "shots",
    )
    by_pid: dict[int, dict[str, object]] = {}

    def ingest(rows: list[dict[str, object]]) -> None:
        for a in rows:
            pl = a["player"]
            assert isinstance(pl, Player)
            if _is_goalie(pl):
                continue
            pid = pl.id
            if pid not in by_pid:
                by_pid[pid] = {**a}
                continue
            m = by_pid[pid]
            for k in keys:
                m[k] = int(m.get(k) or 0) + int(a.get(k) or 0)
            m["plus_minus"] = int(m.get("plus_minus") or 0) + int(a.get("plus_minus") or 0)
            m["points"] = int(m["goals"]) + int(m["assists"])

    ingest(season)
    ingest(career)
    return [a for a in by_pid.values() if int(a.get("gp") or 0) > 0]


def _load_goalie_aggregates(team: Team, stat_segment: str) -> list[dict[str, object]]:
    """Season goalie stats; include every player_id in this table (position may be wrong on Player).

    Same career-vs-season dedup as skaters: skip season rows whose season years overlap
    franchise career CSV lines for that goalie.
    """
    career_sources = (
        FRANCHISE_CAREER_RS_GOALIE
        if stat_segment == "rs"
        else FRANCHISE_CAREER_PO_GOALIE
    )
    q = (
        select(PlayerGoalieStat, Season)
        .join(Season, PlayerGoalieStat.season_id == Season.id)
        .where(
            PlayerGoalieStat.team_id == team.id,
            PlayerGoalieStat.stat_segment == stat_segment,
        )
    )
    raw_rows = db.session.execute(q).all()
    if not raw_rows:
        return []

    pids = list({int(gs.player_id) for gs, _ in raw_rows})
    sids = list({int(gs.season_id) for gs, _ in raw_rows})
    career_years = _franchise_career_years_by_player(
        team, career_sources, PlayerGoalieCareerLine, pids
    )
    game_bounds = _player_team_season_game_year_bounds(
        team.id, pids, sids, GameGoalieStat
    )
    players_by_id = {
        p.id: p
        for p in db.session.scalars(select(Player).where(Player.id.in_(pids))).all()
    }

    acc: dict[int, dict[str, float | int]] = {}
    for stat, season in raw_rows:
        pid = int(stat.player_id)
        sid = int(stat.season_id)
        yset = career_years.get(pid) or set()
        if _should_skip_season_stat_for_franchise_career(
            pid=pid,
            season_id=sid,
            season=season,
            yset=yset,
            pl=players_by_id.get(pid),
            game_bounds=game_bounds,
        ):
            continue
        if pid not in acc:
            acc[pid] = {
                "gp": 0,
                "gs": 0,
                "wins": 0,
                "losses": 0,
                "otl": 0,
                "ga": 0,
                "sa": 0,
                "so": 0,
                "minutes_played": 0.0,
            }
        m = acc[pid]
        m["gp"] = int(m["gp"]) + int(stat.gp or 0)
        m["gs"] = int(m["gs"]) + int(stat.games_started or 0)
        m["wins"] = int(m["wins"]) + int(stat.wins or 0)
        m["losses"] = int(m["losses"]) + int(stat.losses or 0)
        m["otl"] = int(m["otl"]) + int(stat.otl or 0)
        m["ga"] = int(m["ga"]) + int(stat.ga or 0)
        m["sa"] = int(m["sa"]) + int(stat.sa or 0)
        m["so"] = int(m["so"]) + int(stat.so or 0)
        m["minutes_played"] = float(m["minutes_played"]) + float(stat.minutes_played or 0)

    if not acc:
        return []
    players = {
        p.id: p
        for p in db.session.scalars(select(Player).where(Player.id.in_(list(acc)))).all()
    }
    out: list[dict[str, object]] = []
    for pid, m in acc.items():
        pl = players.get(pid)
        if pl is None:
            continue
        gp = int(m["gp"] or 0)
        if gp <= 0:
            continue
        ga = int(m["ga"] or 0)
        sa = int(m["sa"] or 0)
        minutes = float(m["minutes_played"] or 0)
        gaa = _goalie_gaa_from_totals(ga, gp, minutes)
        sv_pct = (float(sa - ga) / float(sa)) if sa > 0 else None
        out.append(
            {
                "player": pl,
                "gp": gp,
                "gs": int(m["gs"] or 0),
                "wins": int(m["wins"] or 0),
                "losses": int(m["losses"] or 0),
                "otl": int(m["otl"] or 0),
                "ga": ga,
                "sa": sa,
                "so": int(m["so"] or 0),
                "minutes_played": minutes,
                "gaa": gaa,
                "sv_pct": sv_pct,
            }
        )
    return out


def _load_goalie_career_franchise(
    team: Team, career_sources: tuple[str, ...]
) -> list[dict[str, object]]:
    """Career goalie lines; do not filter by Player.position (historical data often mislabels G as C/RW)."""
    tc = _team_career_clause(team, PlayerGoalieCareerLine.team_fhm_id, PlayerGoalieCareerLine.team_id)
    if tc is None:
        return []
    league_ids = bowl_nhl_league_ids(db.session)
    if not league_ids:
        league_ids = (0,)
    line = PlayerGoalieCareerLine
    q = (
        select(
            line.player_id.label("pid"),
            func.coalesce(func.sum(line.gp), 0).label("gp"),
            func.coalesce(func.sum(func.coalesce(line.games_started, 0)), 0).label("gs"),
            func.coalesce(func.sum(line.wins), 0).label("wins"),
            func.coalesce(func.sum(line.losses), 0).label("losses"),
            func.coalesce(func.sum(func.coalesce(line.ties_otl, 0)), 0).label("otl"),
            func.coalesce(func.sum(line.goals_against), 0).label("ga"),
            func.coalesce(func.sum(line.shots_against), 0).label("sa"),
            func.coalesce(func.sum(line.shutouts), 0).label("so"),
            func.coalesce(func.sum(func.coalesce(line.minutes_played, 0)), 0).label("minutes_played"),
        )
        .where(
            tc,
            line.league_fhm_id.in_(league_ids),
            line.career_source.in_(career_sources),
        )
        .group_by(line.player_id)
    )
    rows = db.session.execute(q).all()
    if not rows:
        return []
    pids = [int(r.pid) for r in rows]
    players = {
        p.id: p
        for p in db.session.scalars(select(Player).where(Player.id.in_(pids))).all()
    }
    out: list[dict[str, object]] = []
    for r in rows:
        pl = players.get(int(r.pid))
        if pl is None:
            continue
        gp = int(r.gp or 0)
        if gp <= 0:
            continue
        ga = int(r.ga or 0)
        sa = int(r.sa or 0)
        minutes = float(r.minutes_played or 0)
        gaa = _goalie_gaa_from_totals(ga, gp, minutes)
        sv_pct = (float(sa - ga) / float(sa)) if sa > 0 else None
        out.append(
            {
                "player": pl,
                "gp": gp,
                "gs": int(r.gs or 0),
                "wins": int(r.wins or 0),
                "losses": int(r.losses or 0),
                "otl": int(r.otl or 0),
                "ga": ga,
                "sa": sa,
                "so": int(r.so or 0),
                "minutes_played": minutes,
                "gaa": gaa,
                "sv_pct": sv_pct,
            }
        )
    return out


def _merge_goalie_franchise_aggs(
    season: list[dict[str, object]],
    career: list[dict[str, object]],
) -> list[dict[str, object]]:
    sum_keys = ("gp", "gs", "wins", "losses", "otl", "ga", "sa", "so")
    by_pid: dict[int, dict[str, object]] = {}

    def ingest(rows: list[dict[str, object]]) -> None:
        for a in rows:
            pl = a["player"]
            assert isinstance(pl, Player)
            pid = pl.id
            if pid not in by_pid:
                by_pid[pid] = {**a}
                continue
            m = by_pid[pid]
            for k in sum_keys:
                m[k] = int(m.get(k) or 0) + int(a.get(k) or 0)
            m["minutes_played"] = float(m.get("minutes_played") or 0) + float(
                a.get("minutes_played") or 0
            )

    ingest(season)
    ingest(career)
    out: list[dict[str, object]] = []
    for m in by_pid.values():
        gp = int(m.get("gp") or 0)
        if gp <= 0:
            continue
        ga = int(m.get("ga") or 0)
        sa = int(m.get("sa") or 0)
        minutes = float(m.get("minutes_played") or 0)
        m["gaa"] = _goalie_gaa_from_totals(ga, gp, minutes)
        m["sv_pct"] = (float(sa - ga) / float(sa)) if sa > 0 else None
        out.append(m)
    return out


def _card_rows(
    aggs: list[dict[str, object]],
    cat: _LeaderCategory,
    team_abbr: str,
) -> list[dict[str, object]]:
    top = _top_players(aggs, cat.value_fn, higher_better=cat.higher_better)
    rows_out: list[dict[str, object]] = []
    for a, raw in top:
        pl = a["player"]
        assert isinstance(pl, Player)
        val_str = cat.format_fn(raw)
        rows_out.append({"player": pl, "abbr": team_abbr, "value": val_str})
    return rows_out


def _skater_leader_cards(
    aggs: list[dict[str, object]], team_abbr: str
) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    for cat in _skater_categories():
        rows = _card_rows(aggs, cat, team_abbr)
        if rows:
            cards.append({"title": cat.title, "rows": rows})
    return cards


def _goalie_leader_cards(
    aggs: list[dict[str, object]], team_abbr: str
) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = []
    for cat in _goalie_categories():
        rows = _card_rows(aggs, cat, team_abbr)
        if rows:
            cards.append({"title": cat.title, "rows": rows})
    return cards


def build_franchise_history_sections(team: Team) -> list[dict[str, object]]:
    """RS and playoffs: skaters + goalies, merged season stats and career CSVs for this franchise."""
    abbr = (team.abbreviation or "").strip() or "—"
    sections: list[dict[str, object]] = []

    sk_rs = _merge_skater_franchise_aggs(
        _load_skater_aggregates(team, "rs"),
        _load_skater_career_franchise(team, FRANCHISE_CAREER_RS_SKATER),
    )
    sk_rs_cards = _skater_leader_cards(sk_rs, abbr)
    if sk_rs_cards:
        sections.append({"heading": "Skaters — Regular season", "cards": sk_rs_cards})

    gk_rs = _merge_goalie_franchise_aggs(
        _load_goalie_aggregates(team, "rs"),
        _load_goalie_career_franchise(team, FRANCHISE_CAREER_RS_GOALIE),
    )
    gk_rs_cards = _goalie_leader_cards(gk_rs, abbr)
    if gk_rs_cards:
        sections.append({"heading": "Goalies — Regular season", "cards": gk_rs_cards})

    sk_po = _merge_skater_franchise_aggs(
        _load_skater_aggregates(team, "po"),
        _load_skater_career_franchise(team, FRANCHISE_CAREER_PO_SKATER),
    )
    sk_po_cards = _skater_leader_cards(sk_po, abbr)
    if sk_po_cards:
        sections.append({"heading": "Skaters — Playoffs", "cards": sk_po_cards})

    gk_po = _merge_goalie_franchise_aggs(
        _load_goalie_aggregates(team, "po"),
        _load_goalie_career_franchise(team, FRANCHISE_CAREER_PO_GOALIE),
    )
    gk_po_cards = _goalie_leader_cards(gk_po, abbr)
    if gk_po_cards:
        sections.append({"heading": "Goalies — Playoffs", "cards": gk_po_cards})

    return sections
