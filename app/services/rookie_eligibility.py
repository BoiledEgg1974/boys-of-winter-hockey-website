"""NHL-style rookie eligibility helpers (shared by homepage API and game records)."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Iterable

from sqlalchemy import select

from app.models import (
    Player,
    PlayerGoalieCareerLine,
    PlayerSkaterCareerLine,
    Season,
    Team,
)
from app.services.all_time_records import bowl_nhl_league_ids


def season_proxy_for_year(start_year: int) -> SimpleNamespace:
    """Season-shaped object for eligibility when no ``seasons`` row exists."""
    sy = int(start_year)
    return SimpleNamespace(
        start_year=sy,
        end_year=sy + 1,
        label=f"{sy}-{(sy + 1) % 100:02d}",
    )


def rookie_cutoff_date(season: Season) -> date | None:
    if season.start_year:
        return date(season.start_year, 9, 15)
    if season.end_year:
        return date(season.end_year - 1, 9, 15)
    return None


def player_age_years(birth_date: date | None, ref_date: date | None) -> int | None:
    if not birth_date:
        return None
    rd = ref_date or date.today()
    years = rd.year - birth_date.year
    if (rd.month, rd.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years if years >= 0 else None


def is_nhl_style_rookie(
    prior_gp_by_season: Iterable[int],
    birth_date: date | None,
    season: Season,
) -> bool:
    prior = list(prior_gp_by_season or [])
    if any(gp > 25 for gp in prior):
        return False
    if sum(1 for gp in prior if gp >= 6) >= 2:
        return False
    if birth_date:
        cutoff = rookie_cutoff_date(season)
        age = player_age_years(birth_date, cutoff)
        if age is not None and age >= 26:
            return False
    return True


def rookie_stat_team_is_bowl_nhl(team: Team | None, league_ids: tuple[int, ...]) -> bool:
    if team is None:
        return False
    lid = team.fhm_league_id
    if lid is None:
        return True
    try:
        return int(lid) in league_ids
    except (TypeError, ValueError):
        return False


def career_rs_gp_by_season_year_for_players(
    session,
    *,
    player_ids: list[int],
    player_kind: str,
    league_ids: tuple[int, ...] | None = None,
    before_season_year: int | None = None,
) -> dict[int, dict[int, int]]:
    """Regular-season career GP keyed by player and FHM season start year."""
    if not player_ids:
        return {}
    lids = league_ids if league_ids is not None else bowl_nhl_league_ids(session) or (0,)
    model = PlayerGoalieCareerLine if player_kind == "goalie" else PlayerSkaterCareerLine
    filters = [
        model.player_id.in_(player_ids),
        model.career_source.in_(("rs", "retired_rs")),
    ]
    if lids:
        filters.append(model.league_fhm_id.in_(lids))
    if before_season_year is not None:
        filters.append(model.season_year < int(before_season_year))
    rows = session.execute(select(model.player_id, model.season_year, model.gp).where(*filters)).all()
    out: dict[int, dict[int, int]] = {}
    for pid, season_year, gp in rows:
        if season_year is None:
            continue
        pid_i = int(pid)
        yr_i = int(season_year)
        by_year = out.setdefault(pid_i, {})
        by_year[yr_i] = by_year.get(yr_i, 0) + int(gp or 0)
    return out


def prior_skater_gp_by_season_for_players(
    session,
    *,
    player_ids: list[int],
    before_season_year: int,
    league_ids: tuple[int, ...] | None = None,
) -> dict[int, list[int]]:
    by_year = career_rs_gp_by_season_year_for_players(
        session,
        player_ids=player_ids,
        player_kind="skater",
        league_ids=league_ids,
        before_season_year=before_season_year,
    )
    return {pid: list(yrs.values()) for pid, yrs in by_year.items()}


def prior_goalie_gp_by_season_for_players(
    session,
    *,
    player_ids: list[int],
    before_season_year: int,
    league_ids: tuple[int, ...] | None = None,
) -> dict[int, list[int]]:
    by_year = career_rs_gp_by_season_year_for_players(
        session,
        player_ids=player_ids,
        player_kind="goalie",
        league_ids=league_ids,
        before_season_year=before_season_year,
    )
    return {pid: list(yrs.values()) for pid, yrs in by_year.items()}


def player_is_rookie_in_season_year(
    *,
    gp_by_year: dict[int, int] | None,
    birth_date: date | None,
    season_year: int,
) -> bool:
    """True when NHL-style rules say the player was a rookie in ``season_year``."""
    year = int(season_year)
    prior = [gp for yr, gp in (gp_by_year or {}).items() if int(yr) < year]
    return is_nhl_style_rookie(prior, birth_date, season_proxy_for_year(year))


def rookie_player_ids_for_season(
    session,
    season: Season,
    *,
    player_kind: str,
) -> set[int]:
    """Players who qualify as rookies entering ``season`` (NHL-style rules)."""
    before_year = int(season.start_year or season.end_year or 0)
    if before_year <= 0:
        return set()
    league_ids = bowl_nhl_league_ids(session) or (0,)
    if player_kind == "goalie":
        players = session.scalars(select(Player).where((Player.position or "").ilike("G%"))).all()
        prior_map = prior_goalie_gp_by_season_for_players(
            session,
            player_ids=[int(p.id) for p in players],
            before_season_year=before_year,
            league_ids=league_ids,
        )
    else:
        players = session.scalars(
            select(Player).where((Player.position.is_(None)) | (~(Player.position.ilike("G%"))))
        ).all()
        prior_map = prior_skater_gp_by_season_for_players(
            session,
            player_ids=[int(p.id) for p in players],
            before_season_year=before_year,
            league_ids=league_ids,
        )
    out: set[int] = set()
    for pl in players:
        prior = prior_map.get(int(pl.id), [])
        if is_nhl_style_rookie(prior, pl.birth_date, season):
            out.add(int(pl.id))
    return out


def player_was_rookie_in_season(session, player_id: int, season: Season, *, player_kind: str) -> bool:
    return int(player_id) in rookie_player_ids_for_season(session, season, player_kind=player_kind)
