"""Single-season franchise leaderboards for a team (all league team pages)."""

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
from app.services.franchise_leaders import _team_career_clause

TOP_N = 10
_MIN_GP_GOALIE_RATE_RS = 20
_MIN_GP_GOALIE_RATE_PO = 4


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


def _season_row_overlap_years(sn: Season) -> set[int]:
    """Possible career ``season_year`` matches for this season row."""
    yrs: set[int] = set()
    if sn.start_year is not None:
        yrs.add(int(sn.start_year))
    lbl_year = _label_start_year(sn.label)
    if lbl_year is not None:
        yrs.add(lbl_year)
    return yrs


def _season_label(sn: Season) -> str:
    """Short hockey year (e.g. ``1986–87``); never the long DB ``Season.label`` string."""
    sy = _label_start_year(sn.label)
    if sy is not None:
        return f"{sy}–{(sy + 1) % 100:02d}"
    if sn.start_year is not None:
        sy = int(sn.start_year)
        y2 = int(sn.end_year) if sn.end_year is not None else sy + 1
        return f"{sy}–{y2 % 100:02d}"
    if sn.label and str(sn.label).strip():
        return str(sn.label).strip()
    return "—"


def _fmt_sv_pct(val: float | None) -> str:
    if val is None:
        return "—"
    s = f"{float(val):.3f}"
    return s[1:] if s.startswith("0") else s


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


def _top_skater(
    rows: Iterable[tuple[Any, Player, str]],
    *,
    value_fn: Callable[[Any], int | float | None],
    maximize: bool,
    row_filter: Callable[[Any, Player], bool] | None = None,
    fmt: Callable[[float | int], str] = lambda v: str(int(v)) if float(v) == int(v) else str(v),
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, str, str, Player, float | int]] = []
    for st, pl, season_lbl in rows:
        if row_filter is not None and not row_filter(st, pl):
            continue
        raw = value_fn(st)
        if raw is None:
            continue
        if isinstance(raw, float) and raw != raw:  # NaN
            continue
        fv = float(raw)
        scored.append(
            (
                fv,
                (pl.last_name or "").lower(),
                (pl.first_name or "").lower(),
                season_lbl,
                pl,
                raw if isinstance(raw, int) else fv,
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1], x[2], x[3]) if maximize else (x[0], x[1], x[2], x[3]))
    out: list[dict[str, Any]] = []
    for i, (_, _, _, slbl, pl, disp_v) in enumerate(scored[:TOP_N], start=1):
        out.append(
            {
                "rank": i,
                "player": pl,
                "season": slbl,
                "value": fmt(disp_v) if not isinstance(disp_v, str) else disp_v,
            }
        )
    return out


def _top_goalie(
    rows: Iterable[tuple[Any, Player, str]],
    *,
    value_fn: Callable[[Any], int | float | None],
    maximize: bool,
    row_filter: Callable[[Any], bool] | None = None,
    fmt: Callable[[float | int], str] = lambda v: str(int(v)),
) -> list[dict[str, Any]]:
    scored: list[tuple[float, str, str, str, Player, float | int]] = []
    for st, pl, season_lbl in rows:
        if row_filter is not None and not row_filter(st):
            continue
        raw = value_fn(st)
        if raw is None:
            continue
        if isinstance(raw, float) and raw != raw:
            continue
        fv = float(raw)
        scored.append(
            (
                fv,
                (pl.last_name or "").lower(),
                (pl.first_name or "").lower(),
                season_lbl,
                pl,
                raw if isinstance(raw, int) else fv,
            )
        )
    scored.sort(key=lambda x: (-x[0], x[1], x[2], x[3]) if maximize else (x[0], x[1], x[2], x[3]))
    out: list[dict[str, Any]] = []
    for i, (_, _, _, slbl, pl, disp_v) in enumerate(scored[:TOP_N], start=1):
        out.append(
            {
                "rank": i,
                "player": pl,
                "season": slbl,
                "value": fmt(disp_v) if not isinstance(disp_v, str) else disp_v,
            }
        )
    return out


@dataclass(frozen=True)
class TeamSeasonRecordSection:
    title: str
    rows: list[dict[str, Any]]


def _career_row_season_display(session: Session, season_year: int) -> str:
    """Human-readable season for a career CSV ``season_year`` (FHM start year of the hockey season).

    Prefer a narrow ``Season`` row whose label agrees with ``season_year``; otherwise format
    from the FHM year (e.g. 1968 → 1968–69).
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


def _load_skater_rows_merged(session: Session, team: Team, segment: str) -> list[tuple[Any, Player, str]]:
    """Prefer BOWL/NHL skater career CSV rows (active + retired imports); fill gaps from season stat rows."""
    team_id = team.id
    out: list[tuple[Any, Player, str]] = []
    career_year_keys: set[tuple[int, int]] = set()

    tc = _team_career_clause(team, PlayerSkaterCareerLine.team_fhm_id, PlayerSkaterCareerLine.team_id)
    if tc is not None:
        league_ids = bowl_nhl_league_ids(session)
        if not league_ids:
            league_ids = (0,)
        lines = session.scalars(
            select(PlayerSkaterCareerLine).where(
                tc,
                PlayerSkaterCareerLine.league_fhm_id.in_(league_ids),
                PlayerSkaterCareerLine.career_source.in_(_career_sources_for_segment(segment)),
            )
        ).all()
        best: dict[tuple[int, int], PlayerSkaterCareerLine] = {}
        for ln in lines:
            k = (int(ln.player_id), int(ln.season_year))
            cur = best.get(k)
            if cur is None or int(ln.gp) > int(cur.gp):
                best[k] = ln
        players_seen: dict[int, Player] = {}
        for ln in best.values():
            sy = int(ln.season_year)
            career_year_keys.add((int(ln.player_id), sy))
            pl = players_seen.get(ln.player_id)
            if pl is None:
                pl_got = session.get(Player, ln.player_id)
                if pl_got is None:
                    continue
                players_seen[ln.player_id] = pl_got
                pl = pl_got
            lbl = _career_row_season_display(session, sy)
            out.append((_skater_namespace_from_career(ln), pl, lbl))

    players_with_career = {pid for pid, _ in career_year_keys}
    for st, pl, sn in session.execute(
        select(PlayerSkaterStat, Player, Season)
        .join(Player, Player.id == PlayerSkaterStat.player_id)
        .join(Season, Season.id == PlayerSkaterStat.season_id)
        .where(PlayerSkaterStat.team_id == team_id, PlayerSkaterStat.stat_segment == segment)
    ).all():
        if int(st.player_id) in players_with_career:
            continue
        overlap_years = _season_row_overlap_years(sn)
        if any((int(st.player_id), yr) in career_year_keys for yr in overlap_years):
            continue
        out.append((st, pl, _season_label(sn)))
    return out


def _load_goalie_rows_merged(session: Session, team: Team, segment: str) -> list[tuple[Any, Player, str]]:
    """Prefer BOWL/NHL goalie career CSV rows (active + retired imports); fill gaps from season stat rows."""
    team_id = team.id
    out: list[tuple[Any, Player, str]] = []
    career_year_keys: set[tuple[int, int]] = set()

    tc = _team_career_clause(team, PlayerGoalieCareerLine.team_fhm_id, PlayerGoalieCareerLine.team_id)
    if tc is not None:
        league_ids = bowl_nhl_league_ids(session)
        if not league_ids:
            league_ids = (0,)
        lines = session.scalars(
            select(PlayerGoalieCareerLine).where(
                tc,
                PlayerGoalieCareerLine.league_fhm_id.in_(league_ids),
                PlayerGoalieCareerLine.career_source.in_(_career_sources_for_segment(segment)),
            )
        ).all()
        best: dict[tuple[int, int], PlayerGoalieCareerLine] = {}
        for ln in lines:
            k = (int(ln.player_id), int(ln.season_year))
            cur = best.get(k)
            if cur is None or int(ln.gp) > int(cur.gp):
                best[k] = ln
        players_seen: dict[int, Player] = {}
        for ln in best.values():
            sy = int(ln.season_year)
            career_year_keys.add((int(ln.player_id), sy))
            pl = players_seen.get(ln.player_id)
            if pl is None:
                pl_got = session.get(Player, ln.player_id)
                if pl_got is None:
                    continue
                players_seen[ln.player_id] = pl_got
                pl = pl_got
            lbl = _career_row_season_display(session, sy)
            out.append((_goalie_namespace_from_career(ln), pl, lbl))

    players_with_career = {pid for pid, _ in career_year_keys}
    for st, pl, sn in session.execute(
        select(PlayerGoalieStat, Player, Season)
        .join(Player, Player.id == PlayerGoalieStat.player_id)
        .join(Season, Season.id == PlayerGoalieStat.season_id)
        .where(PlayerGoalieStat.team_id == team_id, PlayerGoalieStat.stat_segment == segment)
    ).all():
        if int(st.player_id) in players_with_career:
            continue
        overlap_years = _season_row_overlap_years(sn)
        if any((int(st.player_id), yr) in career_year_keys for yr in overlap_years):
            continue
        out.append((st, pl, _season_label(sn)))
    return out


def _build_skater_sections(
    rows: list[tuple[Any, Player, str]],
) -> list[TeamSeasonRecordSection]:
    sections: list[TeamSeasonRecordSection] = []

    sections.append(
        TeamSeasonRecordSection("Goals", _top_skater(rows, value_fn=lambda s: s.goals, maximize=True))
    )
    sections.append(
        TeamSeasonRecordSection("Assists", _top_skater(rows, value_fn=lambda s: s.assists, maximize=True))
    )
    sections.append(
        TeamSeasonRecordSection("Points", _top_skater(rows, value_fn=lambda s: s.points, maximize=True))
    )
    sections.append(
        TeamSeasonRecordSection(
            "+/-",
            _top_skater(
                rows,
                value_fn=lambda s: s.plus_minus,
                maximize=True,
                row_filter=lambda st, pl: st.plus_minus is not None,
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection("PIM", _top_skater(rows, value_fn=lambda s: s.pim, maximize=True))
    )

    sections.append(
        TeamSeasonRecordSection(
            "PPG",
            _top_skater(
                rows,
                value_fn=lambda s: (s.ppg if s.ppg is not None else 0),
                maximize=True,
                row_filter=lambda st, pl: st.ppg is not None and st.ppg > 0,
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "PP Points",
            _top_skater(
                rows,
                value_fn=lambda s: (s.ppg or 0) + (s.pp_assists or 0),
                maximize=True,
                row_filter=lambda st, pl: ((st.ppg or 0) + (st.pp_assists or 0)) > 0,
            ),
        )
    )

    sections.append(
        TeamSeasonRecordSection(
            "SHG",
            _top_skater(
                rows,
                value_fn=lambda s: (s.shg if s.shg is not None else 0),
                maximize=True,
                row_filter=lambda st, pl: st.shg is not None and st.shg > 0,
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "SH Points",
            _top_skater(
                rows,
                value_fn=lambda s: (s.shg or 0) + (s.sh_assists or 0),
                maximize=True,
                row_filter=lambda st, pl: ((st.shg or 0) + (st.sh_assists or 0)) > 0,
            ),
        )
    )

    sections.append(
        TeamSeasonRecordSection(
            "GWG",
            _top_skater(
                rows,
                value_fn=lambda s: (s.gwg if s.gwg is not None else 0),
                maximize=True,
                row_filter=lambda st, pl: st.gwg is not None and st.gwg > 0,
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "Shots",
            _top_skater(
                rows,
                value_fn=lambda s: s.shots,
                maximize=True,
                row_filter=lambda st, pl: st.shots is not None,
            ),
        )
    )

    sections.append(
        TeamSeasonRecordSection(
            "Hits",
            _top_skater(
                rows,
                value_fn=lambda s: s.hits,
                maximize=True,
                row_filter=lambda st, pl: st.hits is not None,
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "Blocked Shots",
            _top_skater(
                rows,
                value_fn=lambda s: s.blocked_shots,
                maximize=True,
                row_filter=lambda st, pl: st.blocked_shots is not None,
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "Fights",
            _top_skater(
                rows,
                value_fn=lambda s: (s.fights if s.fights is not None else 0),
                maximize=True,
                row_filter=lambda st, pl: st.fights is not None and st.fights > 0,
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "Fights Won",
            _top_skater(
                rows,
                value_fn=lambda s: (s.fights_won if s.fights_won is not None else 0),
                maximize=True,
                row_filter=lambda st, pl: st.fights_won is not None and st.fights_won > 0,
            ),
        )
    )

    return sections


def _build_goalie_sections(rows: list[tuple[Any, Player, str]], segment: str) -> list[TeamSeasonRecordSection]:
    sections: list[TeamSeasonRecordSection] = []
    min_gp_rate = _MIN_GP_GOALIE_RATE_PO if segment == "po" else _MIN_GP_GOALIE_RATE_RS

    sections.append(
        TeamSeasonRecordSection("Goalie Wins", _top_goalie(rows, value_fn=lambda s: s.wins, maximize=True))
    )
    sections.append(
        TeamSeasonRecordSection("Goalie Losses", _top_goalie(rows, value_fn=lambda s: s.losses, maximize=True))
    )
    sections.append(
        TeamSeasonRecordSection(
            "Shots Against",
            _top_goalie(rows, value_fn=lambda s: s.sa, maximize=True),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            f"Lowest GAA (Min. {min_gp_rate} GP)",
            _top_goalie(
                rows,
                value_fn=lambda s: _goalie_gaa(s),
                maximize=False,
                row_filter=lambda st: st.gp >= min_gp_rate and _goalie_gaa(st) is not None,
                fmt=lambda v: f"{float(v):.2f}",
            ),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "Shutouts",
            _top_goalie(rows, value_fn=lambda s: s.so, maximize=True),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            "Saves",
            _top_goalie(rows, value_fn=lambda s: _goalie_saves(s), maximize=True),
        )
    )
    sections.append(
        TeamSeasonRecordSection(
            f"Save % (Min. {min_gp_rate} GP)",
            _top_goalie(
                rows,
                value_fn=lambda s: _goalie_sv_pct(s),
                maximize=True,
                row_filter=lambda st: st.gp >= min_gp_rate and _goalie_sv_pct(st) is not None,
                fmt=lambda v: _fmt_sv_pct(float(v)),
            ),
        )
    )

    return sections


def build_team_season_record_sections(session: Session, team: Team, segment: str) -> list[TeamSeasonRecordSection]:
    """Return ordered skater + goalie sections for RS or PO (``segment`` ``rs`` / ``po``).

    Rows come primarily from **player skater/goalie career** (and **retired** career) CSV
    imports stored in ``PlayerSkaterCareerLine`` / ``PlayerGoalieCareerLine`` for this
    franchise and BOWL/NHL leagues. ``PlayerSkaterStat`` / ``PlayerGoalieStat`` rows for
    this team are used only when there is no matching career line for that player and
    season (e.g. current year not yet in career export).
    """
    sk = _load_skater_rows_merged(session, team, segment)
    gk = _load_goalie_rows_merged(session, team, segment)
    return _build_skater_sections(sk) + _build_goalie_sections(gk, segment)


def build_team_season_records_bundle(session: Session, team: Team) -> tuple[list[TeamSeasonRecordSection], list[TeamSeasonRecordSection]]:
    return (
        build_team_season_record_sections(session, team, "rs"),
        build_team_season_record_sections(session, team, "po"),
    )
