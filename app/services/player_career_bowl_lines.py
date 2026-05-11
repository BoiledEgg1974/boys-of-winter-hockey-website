"""BOWL/NHL-filtered player career lines (same rules as the player profile page)."""
from __future__ import annotations

from typing import Protocol, TypeVar

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import PlayerGoalieCareerLine, PlayerSkaterCareerLine
from app.services.all_time_records import bowl_nhl_league_ids


class _CareerLineDedupeKey(Protocol):
    season_year: int
    team_fhm_id: int
    league_fhm_id: int
    career_source: str


_CDL = TypeVar("_CDL", bound=_CareerLineDedupeKey)


def dedupe_career_lines_by_season_team_league(
    lines: list[_CDL],
    source_rank: dict[str, int],
) -> list[_CDL]:
    """One row per (season_year, team_fhm_id, league_fhm_id); prefer lower *source_rank*."""
    if not lines:
        return []
    ordered = sorted(
        lines,
        key=lambda ln: (
            -ln.season_year,
            ln.team_fhm_id,
            ln.league_fhm_id,
            source_rank.get(ln.career_source, 9),
        ),
    )
    out: list[_CDL] = []
    seen: set[tuple[int, int, int]] = set()
    for ln in ordered:
        key = (ln.season_year, ln.team_fhm_id, ln.league_fhm_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(ln)
    return out


def dedupe_goalie_playoff_career_lines(
    lines: list[PlayerGoalieCareerLine],
) -> list[PlayerGoalieCareerLine]:
    """One playoffs row per (season_year, team_fhm_id, league_fhm_id); prefer *po* over *retired_po*."""
    return dedupe_career_lines_by_season_team_league(lines, {"po": 0, "retired_po": 1})


def load_player_bowl_career_table_lines(
    session: Session,
    player_id: int,
) -> tuple[
    list[PlayerSkaterCareerLine],
    list[PlayerSkaterCareerLine],
    list[PlayerGoalieCareerLine],
    list[PlayerGoalieCareerLine],
]:
    """Return RS/PO skater and goalie career lines restricted to BOWL/NHL league ids."""
    sk_career_lines = session.scalars(
        select(PlayerSkaterCareerLine)
        .options(joinedload(PlayerSkaterCareerLine.team))
        .where(PlayerSkaterCareerLine.player_id == player_id)
        .order_by(PlayerSkaterCareerLine.season_year.desc())
    ).all()
    career_rs_sk = dedupe_career_lines_by_season_team_league(
        [ln for ln in sk_career_lines if ln.career_source in ("rs", "retired_rs")],
        {"rs": 0, "retired_rs": 1},
    )
    career_po_sk = dedupe_career_lines_by_season_team_league(
        [ln for ln in sk_career_lines if ln.career_source in ("po", "retired_po")],
        {"po": 0, "retired_po": 1},
    )

    gk_career_lines = session.scalars(
        select(PlayerGoalieCareerLine)
        .options(joinedload(PlayerGoalieCareerLine.team))
        .where(PlayerGoalieCareerLine.player_id == player_id)
        .order_by(PlayerGoalieCareerLine.season_year.desc())
    ).all()
    career_rs_gk = dedupe_career_lines_by_season_team_league(
        [ln for ln in gk_career_lines if ln.career_source in ("rs", "retired_rs")],
        {"rs": 0, "retired_rs": 1},
    )
    career_po_gk = dedupe_goalie_playoff_career_lines(
        [ln for ln in gk_career_lines if ln.career_source in ("po", "retired_po")]
    )

    main_league_ids = frozenset(bowl_nhl_league_ids(session))
    career_rs_sk = [ln for ln in career_rs_sk if ln.league_fhm_id in main_league_ids]
    career_po_sk = [ln for ln in career_po_sk if ln.league_fhm_id in main_league_ids]
    career_rs_gk = [ln for ln in career_rs_gk if ln.league_fhm_id in main_league_ids]
    career_po_gk = [ln for ln in career_po_gk if ln.league_fhm_id in main_league_ids]

    return career_rs_sk, career_po_sk, career_rs_gk, career_po_gk
