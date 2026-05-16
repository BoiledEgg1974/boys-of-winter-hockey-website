"""League-wide single-season records (RS + PO) for the Season Records page."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Player,
    PlayerGoalieCareerLine,
    PlayerGoalieStat,
    PlayerSkaterCareerLine,
    PlayerSkaterStat,
    Season,
    Team,
)
from app.services.all_time_records import bowl_nhl_league_ids

TOP_N = 10
_MIN_GP_GOALIE_RATE_RS = 20
_MIN_GP_GOALIE_RATE_PO = 4


@dataclass(frozen=True)
class LeagueSeasonRecordSection:
    title: str
    rows: list[dict[str, Any]]


def _label_start_year(label: str | None) -> int | None:
    raw = (label or "").strip()
    if not raw:
        return None
    m = re.search(r"(\d{4})\s*[-–/]\s*(\d{2,4})", raw)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _season_label(sn: Season) -> str:
    sy = _label_start_year(sn.label)
    if sy is not None:
        return f"{sy}–{(sy + 1) % 100:02d}"
    if sn.start_year is not None:
        start = int(sn.start_year)
        end = int(sn.end_year) if sn.end_year is not None else start + 1
        return f"{start}–{end % 100:02d}"
    if sn.label and str(sn.label).strip():
        return str(sn.label).strip()
    return "—"


def _season_overlap_years(sn: Season) -> set[int]:
    out: set[int] = set()
    if sn.start_year is not None:
        out.add(int(sn.start_year))
    sy = _label_start_year(sn.label)
    if sy is not None:
        out.add(sy)
    return out


def _career_row_season_display(session: Session, season_year: int) -> str:
    """Format FHM career ``Year`` (league start year) as a hockey season label.

    Only trust a matching ``Season`` row when it is a narrow span and its label (if any)
    agrees with ``season_year``. Historical mounts reuse ``fhm-league-0`` for a wide
    schedule range; misaligned ``start_year``/label pairs otherwise map 1968 → 1967–68.
    """
    sy = int(season_year)
    hits = session.scalars(select(Season).where(Season.start_year == sy).order_by(Season.id.asc())).all()
    for hit in hits:
        if hit.start_year is None or int(hit.start_year) != sy:
            continue
        end = hit.end_year if hit.end_year is not None else hit.start_year
        if int(end) - int(hit.start_year) > 2:
            continue
        lbl_sy = _label_start_year(hit.label)
        if lbl_sy is not None and lbl_sy != sy:
            continue
        return _season_label(hit)
    return f"{sy}–{(sy + 1) % 100:02d}"


def _goalie_gaa(st: Any) -> float | None:
    if st.gaa is not None:
        return float(st.gaa)
    if st.gp and st.gp > 0:
        return float(st.ga) / float(st.gp)
    return None


def _goalie_sv_pct(st: Any) -> float | None:
    if st.sv_pct is not None:
        return float(st.sv_pct)
    if st.sa and st.sa > 0:
        return float(st.sa - st.ga) / float(st.sa)
    return None


def _goalie_saves(st: Any) -> int:
    return int(st.sa) - int(st.ga)


def _skater_namespace_from_career(ln: PlayerSkaterCareerLine) -> Any:
    pts = int(ln.goals) + int(ln.assists)
    return SimpleNamespace(
        gp=int(ln.gp),
        goals=int(ln.goals),
        assists=int(ln.assists),
        points=pts,
        pim=int(ln.pim),
        plus_minus=ln.plus_minus,
        shots=ln.shots,
        ppg=ln.pp_goals,
        pp_assists=ln.pp_assists,
        shg=ln.sh_goals,
        sh_assists=ln.sh_assists,
        gwg=ln.gwg,
        hits=ln.hits,
        blocked_shots=ln.sb,
        fights=ln.fights,
        fights_won=ln.fights_won,
    )


def _goalie_namespace_from_career(ln: PlayerGoalieCareerLine) -> Any:
    ga = int(ln.goals_against)
    sa = int(ln.shots_against)
    gp = int(ln.gp) if ln.gp else 0
    gaa = (float(ga) / float(gp)) if gp > 0 else None
    sv_pct = (float(sa - ga) / float(sa)) if sa > 0 else None
    return SimpleNamespace(
        gp=gp,
        wins=int(ln.wins),
        losses=int(ln.losses),
        ga=ga,
        sa=sa,
        so=int(ln.shutouts),
        gaa=gaa,
        sv_pct=sv_pct,
    )


def _career_sources_for_segment(segment: str) -> tuple[str, ...]:
    if segment == "rs":
        return ("rs", "retired_rs")
    if segment == "po":
        return ("po", "retired_po")
    return (segment,)


def _team_key(team_id: int | None, team_fhm_id: int | None) -> tuple[int | None, int | None]:
    return (int(team_id) if team_id is not None else None, int(team_fhm_id) if team_fhm_id is not None else None)


def _load_teams_for_fhm_ids(session: Session, fhm_ids: set[int]) -> dict[int, Team]:
    if not fhm_ids:
        return {}
    out: dict[int, Team] = {}
    for t in session.scalars(select(Team).where(Team.fhm_team_id.in_(tuple(str(v) for v in sorted(fhm_ids))))).all():
        if t.fhm_team_id is None:
            continue
        try:
            out[int(str(t.fhm_team_id).strip())] = t
        except ValueError:
            continue
    return out


def _load_skater_rows_merged(session: Session, segment: str) -> list[tuple[Any, Player, Team | None, str]]:
    out: list[tuple[Any, Player, Team | None, str]] = []
    career_keys: set[tuple[int, int, tuple[int | None, int | None]]] = set()

    league_ids = bowl_nhl_league_ids(session) or (0,)
    lines = session.scalars(
        select(PlayerSkaterCareerLine).where(
            PlayerSkaterCareerLine.league_fhm_id.in_(league_ids),
            PlayerSkaterCareerLine.career_source.in_(_career_sources_for_segment(segment)),
        )
    ).all()
    best: dict[tuple[int, int, tuple[int | None, int | None]], PlayerSkaterCareerLine] = {}
    for ln in lines:
        tk = _team_key(ln.team_id, ln.team_fhm_id)
        k = (int(ln.player_id), int(ln.season_year), tk)
        cur = best.get(k)
        if cur is None or int(ln.gp) > int(cur.gp):
            best[k] = ln

    team_ids = {int(ln.team_id) for ln in best.values() if ln.team_id is not None}
    team_fhms = {int(ln.team_fhm_id) for ln in best.values() if ln.team_fhm_id is not None}
    teams_by_id = {t.id: t for t in session.scalars(select(Team).where(Team.id.in_(tuple(sorted(team_ids))))).all()} if team_ids else {}
    teams_by_fhm = _load_teams_for_fhm_ids(session, team_fhms)

    players_seen: dict[int, Player] = {}
    for (pid, sy, tk), ln in best.items():
        career_keys.add((pid, sy, tk))
        pl = players_seen.get(pid)
        if pl is None:
            got = session.get(Player, pid)
            if got is None:
                continue
            players_seen[pid] = got
            pl = got
        tm = teams_by_id.get(int(ln.team_id)) if ln.team_id is not None else None
        if tm is None and ln.team_fhm_id is not None:
            tm = teams_by_fhm.get(int(ln.team_fhm_id))
        out.append((_skater_namespace_from_career(ln), pl, tm, _career_row_season_display(session, sy)))

    players_with_career = {pid for pid, _, _ in career_keys}
    for st, pl, sn, tm in session.execute(
        select(PlayerSkaterStat, Player, Season, Team)
        .join(Player, Player.id == PlayerSkaterStat.player_id)
        .join(Season, Season.id == PlayerSkaterStat.season_id)
        .join(Team, Team.id == PlayerSkaterStat.team_id)
        .where(PlayerSkaterStat.stat_segment == segment)
    ).all():
        if int(st.player_id) in players_with_career:
            continue
        years = _season_overlap_years(sn) or set()
        tk = _team_key(st.team_id, int(str(tm.fhm_team_id).strip()) if tm.fhm_team_id and str(tm.fhm_team_id).strip().isdigit() else None)
        if any((int(st.player_id), yr, tk) in career_keys for yr in years):
            continue
        out.append((st, pl, tm, _season_label(sn)))
    return out


def _load_goalie_rows_merged(session: Session, segment: str) -> list[tuple[Any, Player, Team | None, str]]:
    out: list[tuple[Any, Player, Team | None, str]] = []
    career_keys: set[tuple[int, int, tuple[int | None, int | None]]] = set()

    league_ids = bowl_nhl_league_ids(session) or (0,)
    lines = session.scalars(
        select(PlayerGoalieCareerLine).where(
            PlayerGoalieCareerLine.league_fhm_id.in_(league_ids),
            PlayerGoalieCareerLine.career_source.in_(_career_sources_for_segment(segment)),
        )
    ).all()
    best: dict[tuple[int, int, tuple[int | None, int | None]], PlayerGoalieCareerLine] = {}
    for ln in lines:
        tk = _team_key(ln.team_id, ln.team_fhm_id)
        k = (int(ln.player_id), int(ln.season_year), tk)
        cur = best.get(k)
        if cur is None or int(ln.gp) > int(cur.gp):
            best[k] = ln

    team_ids = {int(ln.team_id) for ln in best.values() if ln.team_id is not None}
    team_fhms = {int(ln.team_fhm_id) for ln in best.values() if ln.team_fhm_id is not None}
    teams_by_id = {t.id: t for t in session.scalars(select(Team).where(Team.id.in_(tuple(sorted(team_ids))))).all()} if team_ids else {}
    teams_by_fhm = _load_teams_for_fhm_ids(session, team_fhms)

    players_seen: dict[int, Player] = {}
    for (pid, sy, tk), ln in best.items():
        career_keys.add((pid, sy, tk))
        pl = players_seen.get(pid)
        if pl is None:
            got = session.get(Player, pid)
            if got is None:
                continue
            players_seen[pid] = got
            pl = got
        tm = teams_by_id.get(int(ln.team_id)) if ln.team_id is not None else None
        if tm is None and ln.team_fhm_id is not None:
            tm = teams_by_fhm.get(int(ln.team_fhm_id))
        out.append((_goalie_namespace_from_career(ln), pl, tm, _career_row_season_display(session, sy)))

    players_with_career = {pid for pid, _, _ in career_keys}
    for st, pl, sn, tm in session.execute(
        select(PlayerGoalieStat, Player, Season, Team)
        .join(Player, Player.id == PlayerGoalieStat.player_id)
        .join(Season, Season.id == PlayerGoalieStat.season_id)
        .join(Team, Team.id == PlayerGoalieStat.team_id)
        .where(PlayerGoalieStat.stat_segment == segment)
    ).all():
        if int(st.player_id) in players_with_career:
            continue
        years = _season_overlap_years(sn) or set()
        tk = _team_key(st.team_id, int(str(tm.fhm_team_id).strip()) if tm.fhm_team_id and str(tm.fhm_team_id).strip().isdigit() else None)
        if any((int(st.player_id), yr, tk) in career_keys for yr in years):
            continue
        out.append((st, pl, tm, _season_label(sn)))
    return out


def _top_skater(
    rows: Iterable[tuple[Any, Player, Team | None, str]],
    *,
    value_fn: Callable[[Any], int | float | None],
    maximize: bool,
    row_filter: Callable[[Any], bool] | None = None,
    fmt: Callable[[float | int], str] = lambda v: str(int(v)) if float(v) == int(v) else str(v),
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, str, str, str, Player, Team | None, float | int]] = []
    for st, pl, tm, season_lbl in rows:
        if row_filter is not None and not row_filter(st):
            continue
        raw = value_fn(st)
        if raw is None:
            continue
        fv = float(raw)
        if fv != fv:
            continue
        tkey = (tm.abbreviation or tm.name).lower() if tm is not None else ""
        scored.append((fv, (pl.last_name or "").lower(), (pl.first_name or "").lower(), tkey, season_lbl, pl, tm, raw if isinstance(raw, int) else fv))
    scored.sort(key=lambda x: (-x[0], x[1], x[2], x[3], x[4]) if maximize else (x[0], x[1], x[2], x[3], x[4]))
    out: list[dict[str, Any]] = []
    for i, (_, _, _, _, slbl, pl, tm, disp_v) in enumerate(scored[:TOP_N], start=1):
        out.append({"rank": i, "player": pl, "team": tm, "season": slbl, "value": fmt(disp_v) if not isinstance(disp_v, str) else disp_v})
    return out


def _top_goalie(
    rows: Iterable[tuple[Any, Player, Team | None, str]],
    *,
    value_fn: Callable[[Any], int | float | None],
    maximize: bool,
    row_filter: Callable[[Any], bool] | None = None,
    fmt: Callable[[float | int], str] = lambda v: str(int(v)),
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, str, str, str, Player, Team | None, float | int]] = []
    for st, pl, tm, season_lbl in rows:
        if row_filter is not None and not row_filter(st):
            continue
        raw = value_fn(st)
        if raw is None:
            continue
        fv = float(raw)
        if fv != fv:
            continue
        tkey = (tm.abbreviation or tm.name).lower() if tm is not None else ""
        scored.append((fv, (pl.last_name or "").lower(), (pl.first_name or "").lower(), tkey, season_lbl, pl, tm, raw if isinstance(raw, int) else fv))
    scored.sort(key=lambda x: (-x[0], x[1], x[2], x[3], x[4]) if maximize else (x[0], x[1], x[2], x[3], x[4]))
    out: list[dict[str, Any]] = []
    for i, (_, _, _, _, slbl, pl, tm, disp_v) in enumerate(scored[:TOP_N], start=1):
        out.append({"rank": i, "player": pl, "team": tm, "season": slbl, "value": fmt(disp_v) if not isinstance(disp_v, str) else disp_v})
    return out


def _build_skater_sections(rows: list[tuple[Any, Player, Team | None, str]]) -> list[LeagueSeasonRecordSection]:
    return [
        LeagueSeasonRecordSection("Goals", _top_skater(rows, value_fn=lambda s: s.goals, maximize=True)),
        LeagueSeasonRecordSection("Assists", _top_skater(rows, value_fn=lambda s: s.assists, maximize=True)),
        LeagueSeasonRecordSection("Points", _top_skater(rows, value_fn=lambda s: s.points, maximize=True)),
        LeagueSeasonRecordSection("+/-", _top_skater(rows, value_fn=lambda s: s.plus_minus, maximize=True, row_filter=lambda st: st.plus_minus is not None)),
        LeagueSeasonRecordSection("PIM", _top_skater(rows, value_fn=lambda s: s.pim, maximize=True)),
        LeagueSeasonRecordSection("PPG", _top_skater(rows, value_fn=lambda s: (s.ppg if s.ppg is not None else 0), maximize=True, row_filter=lambda st: st.ppg is not None and st.ppg > 0)),
        LeagueSeasonRecordSection("PP Points", _top_skater(rows, value_fn=lambda s: (s.ppg or 0) + (s.pp_assists or 0), maximize=True, row_filter=lambda st: ((st.ppg or 0) + (st.pp_assists or 0)) > 0)),
        LeagueSeasonRecordSection("SHG", _top_skater(rows, value_fn=lambda s: (s.shg if s.shg is not None else 0), maximize=True, row_filter=lambda st: st.shg is not None and st.shg > 0)),
        LeagueSeasonRecordSection("SH Points", _top_skater(rows, value_fn=lambda s: (s.shg or 0) + (s.sh_assists or 0), maximize=True, row_filter=lambda st: ((st.shg or 0) + (st.sh_assists or 0)) > 0)),
        LeagueSeasonRecordSection("GWG", _top_skater(rows, value_fn=lambda s: (s.gwg if s.gwg is not None else 0), maximize=True, row_filter=lambda st: st.gwg is not None and st.gwg > 0)),
        LeagueSeasonRecordSection("Shots", _top_skater(rows, value_fn=lambda s: s.shots, maximize=True, row_filter=lambda st: st.shots is not None)),
        LeagueSeasonRecordSection("Hits", _top_skater(rows, value_fn=lambda s: s.hits, maximize=True, row_filter=lambda st: st.hits is not None)),
        LeagueSeasonRecordSection("Blocked Shots", _top_skater(rows, value_fn=lambda s: s.blocked_shots, maximize=True, row_filter=lambda st: st.blocked_shots is not None)),
        LeagueSeasonRecordSection("Fights", _top_skater(rows, value_fn=lambda s: (s.fights if s.fights is not None else 0), maximize=True, row_filter=lambda st: st.fights is not None and st.fights > 0)),
        LeagueSeasonRecordSection("Fights Won", _top_skater(rows, value_fn=lambda s: (s.fights_won if s.fights_won is not None else 0), maximize=True, row_filter=lambda st: st.fights_won is not None and st.fights_won > 0)),
    ]


def _build_goalie_sections(rows: list[tuple[Any, Player, Team | None, str]], segment: str) -> list[LeagueSeasonRecordSection]:
    min_gp_rate = _MIN_GP_GOALIE_RATE_PO if segment == "po" else _MIN_GP_GOALIE_RATE_RS
    return [
        LeagueSeasonRecordSection("Goalie Wins", _top_goalie(rows, value_fn=lambda s: s.wins, maximize=True)),
        LeagueSeasonRecordSection("Goalie Losses", _top_goalie(rows, value_fn=lambda s: s.losses, maximize=True)),
        LeagueSeasonRecordSection("Shots Against", _top_goalie(rows, value_fn=lambda s: s.sa, maximize=True)),
        LeagueSeasonRecordSection(f"Lowest GAA (Min. {min_gp_rate} GP)", _top_goalie(rows, value_fn=lambda s: _goalie_gaa(s), maximize=False, row_filter=lambda st: st.gp >= min_gp_rate and _goalie_gaa(st) is not None, fmt=lambda v: f"{float(v):.2f}")),
        LeagueSeasonRecordSection("Shutouts", _top_goalie(rows, value_fn=lambda s: s.so, maximize=True)),
        LeagueSeasonRecordSection("Saves", _top_goalie(rows, value_fn=lambda s: _goalie_saves(s), maximize=True)),
        LeagueSeasonRecordSection(f"Save % (Min. {min_gp_rate} GP)", _top_goalie(rows, value_fn=lambda s: _goalie_sv_pct(s), maximize=True, row_filter=lambda st: st.gp >= min_gp_rate and _goalie_sv_pct(st) is not None, fmt=lambda v: f"{float(v):.3f}".lstrip("0"))),
    ]


def build_league_season_record_sections(session: Session, segment: str) -> list[LeagueSeasonRecordSection]:
    sk = _load_skater_rows_merged(session, segment)
    gk = _load_goalie_rows_merged(session, segment)
    return _build_skater_sections(sk) + _build_goalie_sections(gk, segment)


def build_league_season_records_bundle(session: Session) -> tuple[list[LeagueSeasonRecordSection], list[LeagueSeasonRecordSection]]:
    return (build_league_season_record_sections(session, "rs"), build_league_season_record_sections(session, "po"))

