"""Auto-sync TeamSeasonRecord rows from imported career lines.

The Team Records page reads ``TeamSeasonRecord`` in the database. Historical CSV
(``team_season_records_template.csv``) remains the source of truth when present;
this module fills gaps for completed seasons that exist in FHM career imports but
were never added to the CSV.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    PlayerGoalieCareerLine,
    PlayerSkaterCareerLine,
    Team,
    TeamSeasonRecord,
)
from app.services.admin_history_records import (
    HISTORY_SOURCE_CSV,
    HISTORY_SOURCE_IMPORT,
)
from app.services.all_time_records import bowl_nhl_league_ids
from app.services.seasons import get_current_season

log = logging.getLogger(__name__)

_CAREER_RS = ("rs", "retired_rs")
_CAREER_PO = ("po", "retired_po")

_PLAYOFF_RESULT_BY_MAX_PO_GP: dict[int, str] = {
    26: "BOWL CUP CHAMPION",
    21: "Lost Cup Finals",
    19: "Lost Conference Finals",
    16: "Lost Conference Finals",
    13: "Lost Conference Semi-Finals",
    12: "Lost Conference Semi-Finals",
    7: "Lost Conference Quarter-Finals",
    6: "Lost Conference Quarter-Finals",
    5: "Lost Conference Quarter-Finals",
}

_EASTERN_DIVS = ("Northeast", "Atlantic", "Southeast")
_WESTERN_DIVS = ("Central", "Pacific", "Northwest")


@dataclass
class _TeamAgg:
    team_fhm_id: str
    team_id: int | None = None
    w: int = 0
    l: int = 0
    otl: int = 0
    gf: int = 0
    ga: int = 0
    pim: int = 0
    ppg: int = 0
    shg: int = 0
    sog: int = 0
    sa: int = 0
    conf_id: int | None = None
    div_id: int | None = None
    max_po_gp: int = 0

    @property
    def gp(self) -> int:
        return self.w + self.l + self.otl

    @property
    def pts(self) -> int:
        return self.w * 2 + self.otl

    @property
    def goal_diff(self) -> int:
        return self.gf - self.ga

    def playoff_result(self) -> str:
        if self.max_po_gp <= 0:
            return "Missed Playoffs"
        return _PLAYOFF_RESULT_BY_MAX_PO_GP.get(self.max_po_gp, "Made Playoffs")


def _year_label(start_year: int) -> str:
    return f"{start_year}-{(start_year + 1) % 100:02d}"


def _conf_div_names(conf_id: int | None, div_id: int | None) -> tuple[str | None, str | None]:
    if conf_id is None:
        return None, None
    conf = "Eastern" if int(conf_id) == 0 else "Western"
    if div_id is None:
        return conf, None
    divs = _EASTERN_DIVS if int(conf_id) == 0 else _WESTERN_DIVS
    idx = int(div_id)
    if 0 <= idx < len(divs):
        return conf, divs[idx]
    return conf, None


def _load_team_meta_from_csv(raw_dir: Path | None) -> dict[str, tuple[int | None, int | None]]:
    """Map FHM team id -> (conference_id, division_id) from ``team_data.csv``."""
    out: dict[str, tuple[int | None, int | None]] = {}
    if raw_dir is None:
        return out
    path = raw_dir / "team_data.csv"
    if not path.is_file():
        return out
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            tid = (row.get("TeamId") or "").strip()
            if not tid:
                continue
            try:
                conf = int((row.get("Conference Id") or "").strip() or "0")
            except ValueError:
                conf = None
            try:
                div = int((row.get("Division Id") or "").strip() or "0")
            except ValueError:
                div = None
            out[tid] = (conf, div)
    return out


def _career_season_years(session: Session, league_ids: tuple[int, ...]) -> set[int]:
    years: set[int] = set()
    for model in (PlayerSkaterCareerLine, PlayerGoalieCareerLine):
        for sy in session.scalars(
            select(model.season_year)
            .where(
                model.league_fhm_id.in_(league_ids),
                model.career_source.in_(_CAREER_RS),
            )
            .distinct()
        ).all():
            if sy is not None:
                years.add(int(sy))
    return years


def _aggregate_career_year(
    session: Session,
    *,
    season_year: int,
    league_ids: tuple[int, ...],
    team_meta: dict[str, tuple[int | None, int | None]],
) -> dict[str, _TeamAgg]:
    by_fhm: dict[str, _TeamAgg] = {}

    def ensure(fhm: str) -> _TeamAgg:
        if fhm not in by_fhm:
            conf_id, div_id = team_meta.get(fhm, (None, None))
            team = session.scalars(select(Team).where(Team.fhm_team_id == fhm).limit(1)).first()
            if team is not None:
                conf_id = team.fhm_conference_id if conf_id is None else conf_id
                div_id = team.fhm_division_id if div_id is None else div_id
            by_fhm[fhm] = _TeamAgg(
                team_fhm_id=fhm,
                team_id=int(team.id) if team is not None else None,
                conf_id=conf_id,
                div_id=div_id,
            )
        return by_fhm[fhm]

    for ln in session.scalars(
        select(PlayerGoalieCareerLine).where(
            PlayerGoalieCareerLine.season_year == int(season_year),
            PlayerGoalieCareerLine.league_fhm_id.in_(league_ids),
            PlayerGoalieCareerLine.career_source.in_(_CAREER_RS),
        )
    ).all():
        fhm = str(ln.team_fhm_id or "").strip()
        if not fhm:
            continue
        t = ensure(fhm)
        t.w += int(ln.wins or 0)
        t.l += int(ln.losses or 0)
        t.otl += int(ln.ties_otl or 0)
        t.ga += int(ln.goals_against or 0)
        t.sa += int(ln.shots_against or 0)

    for ln in session.scalars(
        select(PlayerSkaterCareerLine).where(
            PlayerSkaterCareerLine.season_year == int(season_year),
            PlayerSkaterCareerLine.league_fhm_id.in_(league_ids),
            PlayerSkaterCareerLine.career_source.in_(_CAREER_RS),
        )
    ).all():
        fhm = str(ln.team_fhm_id or "").strip()
        if not fhm:
            continue
        t = ensure(fhm)
        t.gf += int(ln.goals or 0)
        t.pim += int(ln.pim or 0)
        t.ppg += int(ln.pp_goals or 0)
        t.shg += int(ln.sh_goals or 0)
        t.sog += int(ln.shots or 0)

    for ln in session.scalars(
        select(PlayerSkaterCareerLine).where(
            PlayerSkaterCareerLine.season_year == int(season_year),
            PlayerSkaterCareerLine.league_fhm_id.in_(league_ids),
            PlayerSkaterCareerLine.career_source.in_(_CAREER_PO),
        )
    ).all():
        fhm = str(ln.team_fhm_id or "").strip()
        if not fhm:
            continue
        t = ensure(fhm)
        t.max_po_gp = max(t.max_po_gp, int(ln.gp or 0))

    return {
        fhm: agg
        for fhm, agg in by_fhm.items()
        if agg.gp > 0 or agg.gf > 0 or agg.ga > 0
    }


def _protected_keys(session: Session) -> set[tuple[str, int | None, str | None]]:
    """Rows that must not be overwritten by import sync."""
    keys: set[tuple[str, int | None, str | None]] = set()
    for rec in session.scalars(select(TeamSeasonRecord)).all():
        src = (rec.source or HISTORY_SOURCE_CSV).strip().lower()
        if src in (HISTORY_SOURCE_CSV, "admin"):
            keys.add(
                (
                    rec.season_year_label,
                    rec.team_id,
                    (rec.team_name_override or "").strip() or None,
                )
            )
    return keys


def _csv_covered_year_labels(session: Session) -> set[str]:
    """Season labels that already have authoritative CSV team-season rows."""
    out: set[str] = set()
    for rec in session.scalars(select(TeamSeasonRecord)).all():
        src = (rec.source or HISTORY_SOURCE_CSV).strip().lower()
        if src == HISTORY_SOURCE_CSV:
            out.add(rec.season_year_label)
    return out


def _purge_import_rows_for_csv_seasons(session: Session) -> int:
    """Remove import-sync duplicates when CSV already covers that season."""
    csv_seasons = _csv_covered_year_labels(session)
    if not csv_seasons:
        return 0
    removed = 0
    for rec in list(session.scalars(select(TeamSeasonRecord)).all()):
        if (rec.source or "").strip().lower() != HISTORY_SOURCE_IMPORT:
            continue
        if rec.season_year_label in csv_seasons:
            session.delete(rec)
            removed += 1
    return removed


def _upsert_import_row(session: Session, *, year_label: str, start_year: int, agg: _TeamAgg) -> bool:
    conf, div = _conf_div_names(agg.conf_id, agg.div_id)
    pim_g = round(agg.pim / agg.gp, 2) if agg.gp > 0 and agg.pim > 0 else None
    q = select(TeamSeasonRecord).where(
        TeamSeasonRecord.season_year_label == year_label,
        TeamSeasonRecord.source == HISTORY_SOURCE_IMPORT,
    )
    if agg.team_id is not None:
        q = q.where(TeamSeasonRecord.team_id == int(agg.team_id))
    else:
        q = q.where(
            TeamSeasonRecord.team_id.is_(None),
            TeamSeasonRecord.team_fhm_id_csv == agg.team_fhm_id,
        )
    rec = session.scalars(q.limit(1)).first()
    if rec is None:
        rec = TeamSeasonRecord(
            season_year_label=year_label,
            start_year=start_year,
            team_id=agg.team_id,
            team_fhm_id_csv=agg.team_fhm_id,
            source=HISTORY_SOURCE_IMPORT,
        )
        session.add(rec)
    rec.conference_id = agg.conf_id
    rec.conference_override = conf
    rec.division_id = agg.div_id
    rec.division_override = div
    rec.gp = agg.gp or None
    rec.w = agg.w or None
    rec.l = agg.l or None
    rec.t_otl = agg.otl or None
    rec.pts = agg.pts or None
    rec.gf = agg.gf or None
    rec.ga = agg.ga or None
    rec.goal_diff = agg.goal_diff if agg.gp > 0 else None
    rec.result = agg.playoff_result()
    rec.pim_per_game = pim_g
    rec.ppg = agg.ppg or None
    rec.shg = agg.shg or None
    rec.shots_for = agg.sog or None
    rec.shots_against = agg.sa or None
    return True


def sync_team_season_records_from_import(
    session: Session,
    raw_dir: Path | None = None,
) -> int:
    """Upsert import-sourced team season rows from completed-season career data.

    Returns the number of rows written.
    """
    league_ids = bowl_nhl_league_ids(session) or (0,)
    team_meta = _load_team_meta_from_csv(raw_dir)
    protected = _protected_keys(session)
    csv_seasons = _csv_covered_year_labels(session)
    purged = _purge_import_rows_for_csv_seasons(session)
    if purged:
        log.info(
            "Removed %s import-sourced team_season_records row(s) superseded by CSV data.",
            purged,
        )
    current = get_current_season(session)
    current_start = int(current.start_year) if current and current.start_year is not None else None

    years = sorted(_career_season_years(session, league_ids))

    written = 0
    for season_year in years:
        year_label = _year_label(season_year)
        if year_label in csv_seasons:
            continue
        is_current = current_start is not None and int(season_year) == int(current_start)
        if is_current:
            continue

        aggs = _aggregate_career_year(
            session,
            season_year=season_year,
            league_ids=league_ids,
            team_meta=team_meta,
        )

        if not aggs:
            continue

        for agg in aggs.values():
            key = (year_label, agg.team_id, None)
            if key in protected:
                continue
            if _upsert_import_row(session, year_label=year_label, start_year=season_year, agg=agg):
                written += 1

    if written:
        log.info("Synced %s import-sourced team_season_records row(s) from career/standings.", written)
    return written
