"""Franchise history leader boards: RS and playoffs from season stats + BOWL/NHL career CSVs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import func, or_, select

from app.models import (
    Player,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
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


def _load_skater_aggregates(team_id: int, stat_segment: str) -> list[dict[str, object]]:
    q = (
        select(
            PlayerSkaterStat.player_id.label("pid"),
            func.sum(PlayerSkaterStat.gp).label("gp"),
            func.sum(PlayerSkaterStat.goals).label("goals"),
            func.sum(PlayerSkaterStat.assists).label("assists"),
            func.sum(PlayerSkaterStat.points).label("points"),
            func.sum(func.coalesce(PlayerSkaterStat.ppg, 0)).label("ppg"),
            func.sum(func.coalesce(PlayerSkaterStat.pp_assists, 0)).label("ppa"),
            func.sum(func.coalesce(PlayerSkaterStat.shg, 0)).label("shg"),
            func.sum(func.coalesce(PlayerSkaterStat.sh_assists, 0)).label("sha"),
            func.sum(func.coalesce(PlayerSkaterStat.gwg, 0)).label("gwg"),
            func.sum(PlayerSkaterStat.plus_minus).label("plus_minus"),
            func.sum(PlayerSkaterStat.pim).label("pim"),
            func.sum(func.coalesce(PlayerSkaterStat.hits, 0)).label("hits"),
            func.sum(func.coalesce(PlayerSkaterStat.fights, 0)).label("fights"),
            func.sum(func.coalesce(PlayerSkaterStat.blocked_shots, 0)).label("blocked_shots"),
            func.sum(func.coalesce(PlayerSkaterStat.shots, 0)).label("shots"),
        )
        .where(
            PlayerSkaterStat.team_id == team_id,
            PlayerSkaterStat.stat_segment == stat_segment,
        )
        .group_by(PlayerSkaterStat.player_id)
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
        pm = r.plus_minus
        pm_i = int(pm) if pm is not None else None
        goals = int(r.goals or 0)
        assists = int(r.assists or 0)
        pts = int(r.points or 0) if r.points is not None else goals + assists
        out.append(
            {
                "player": pl,
                "gp": gp,
                "goals": goals,
                "assists": assists,
                "points": pts,
                "ppg": int(r.ppg or 0),
                "ppa": int(r.ppa or 0),
                "shg": int(r.shg or 0),
                "sha": int(r.sha or 0),
                "gwg": int(r.gwg or 0),
                "plus_minus": pm_i,
                "pim": int(r.pim or 0),
                "hits": int(r.hits or 0),
                "fights": int(r.fights or 0),
                "blocked_shots": int(r.blocked_shots or 0),
                "shots": int(r.shots or 0),
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
    """Sum season DB totals and career-export totals per player (retired + active)."""
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


def _load_goalie_aggregates(team_id: int, stat_segment: str) -> list[dict[str, object]]:
    """Season goalie stats; include every player_id in this table (position may be wrong on Player)."""
    q = (
        select(
            PlayerGoalieStat.player_id.label("pid"),
            func.sum(PlayerGoalieStat.gp).label("gp"),
            func.sum(func.coalesce(PlayerGoalieStat.games_started, 0)).label("gs"),
            func.sum(PlayerGoalieStat.wins).label("wins"),
            func.sum(PlayerGoalieStat.losses).label("losses"),
            func.sum(PlayerGoalieStat.otl).label("otl"),
            func.sum(PlayerGoalieStat.ga).label("ga"),
            func.sum(PlayerGoalieStat.sa).label("sa"),
            func.sum(PlayerGoalieStat.so).label("so"),
            func.sum(func.coalesce(PlayerGoalieStat.minutes_played, 0)).label("minutes_played"),
        )
        .where(
            PlayerGoalieStat.team_id == team_id,
            PlayerGoalieStat.stat_segment == stat_segment,
        )
        .group_by(PlayerGoalieStat.player_id)
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
        gaa = (float(ga) * 60.0 / minutes) if minutes > 0 else None
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
        gaa = (float(ga) * 60.0 / minutes) if minutes > 0 else None
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
        m["gaa"] = (float(ga) * 60.0 / minutes) if minutes > 0 else None
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
        _load_skater_aggregates(team.id, "rs"),
        _load_skater_career_franchise(team, FRANCHISE_CAREER_RS_SKATER),
    )
    sk_rs_cards = _skater_leader_cards(sk_rs, abbr)
    if sk_rs_cards:
        sections.append({"heading": "Skaters — Regular season", "cards": sk_rs_cards})

    gk_rs = _merge_goalie_franchise_aggs(
        _load_goalie_aggregates(team.id, "rs"),
        _load_goalie_career_franchise(team, FRANCHISE_CAREER_RS_GOALIE),
    )
    gk_rs_cards = _goalie_leader_cards(gk_rs, abbr)
    if gk_rs_cards:
        sections.append({"heading": "Goalies — Regular season", "cards": gk_rs_cards})

    sk_po = _merge_skater_franchise_aggs(
        _load_skater_aggregates(team.id, "po"),
        _load_skater_career_franchise(team, FRANCHISE_CAREER_PO_SKATER),
    )
    sk_po_cards = _skater_leader_cards(sk_po, abbr)
    if sk_po_cards:
        sections.append({"heading": "Skaters — Playoffs", "cards": sk_po_cards})

    gk_po = _merge_goalie_franchise_aggs(
        _load_goalie_aggregates(team.id, "po"),
        _load_goalie_career_franchise(team, FRANCHISE_CAREER_PO_GOALIE),
    )
    gk_po_cards = _goalie_leader_cards(gk_po, abbr)
    if gk_po_cards:
        sections.append({"heading": "Goalies — Playoffs", "cards": gk_po_cards})

    return sections
