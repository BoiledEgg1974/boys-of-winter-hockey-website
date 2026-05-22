"""Team alumni derived from imported career stat lines."""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerGoalieCareerLine, PlayerSkaterCareerLine, Team

REGULAR_SEASON_CAREER_SOURCES = ("rs", "retired_rs")
REGULAR_SEASON_SOURCE_RANK = {"rs": 0, "retired_rs": 1}


@dataclass(frozen=True)
class TeamAlumniRow:
    player: Player
    kind: str
    seasons: int
    first_year: int | None
    last_year: int | None
    gp: int
    goals: int | None = None
    assists: int | None = None
    points: int | None = None
    wins: int | None = None
    shutouts: int | None = None


def _dedupe_alumni_regular_season_lines(lines: list):
    """One regular-season row per player/team/season/league, preferring active rows over retired duplicates."""
    ordered = sorted(
        lines,
        key=lambda ln: (
            int(getattr(ln, "player_id", 0) or 0),
            int(getattr(ln, "season_year", 0) or 0),
            int(getattr(ln, "team_fhm_id", 0) or 0),
            int(getattr(ln, "league_fhm_id", 0) or 0),
            REGULAR_SEASON_SOURCE_RANK.get(str(getattr(ln, "career_source", "")), 9),
        ),
    )
    out = []
    seen: set[tuple[int, int, int, int]] = set()
    for line in ordered:
        key = (
            int(getattr(line, "player_id", 0) or 0),
            int(getattr(line, "season_year", 0) or 0),
            int(getattr(line, "team_fhm_id", 0) or 0),
            int(getattr(line, "league_fhm_id", 0) or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out


def team_alumni_rows(session: Session, team: Team, *, limit: int | None = None) -> list[TeamAlumniRow]:
    """Return former players with career games for this team.

    Current roster players are excluded so the tab reads as alumni rather than
    a duplicate roster/statistics view.
    """
    skater_lines = session.scalars(
        select(PlayerSkaterCareerLine)
        .join(Player, Player.id == PlayerSkaterCareerLine.player_id)
        .where(
            PlayerSkaterCareerLine.team_id == team.id,
            PlayerSkaterCareerLine.career_source.in_(REGULAR_SEASON_CAREER_SOURCES),
            Player.current_team_id.is_distinct_from(team.id),
        )
    ).all()
    goalie_lines = session.scalars(
        select(PlayerGoalieCareerLine)
        .join(Player, Player.id == PlayerGoalieCareerLine.player_id)
        .where(
            PlayerGoalieCareerLine.team_id == team.id,
            PlayerGoalieCareerLine.career_source.in_(REGULAR_SEASON_CAREER_SOURCES),
            Player.current_team_id.is_distinct_from(team.id),
        )
    ).all()
    skater_lines = _dedupe_alumni_regular_season_lines(list(skater_lines))
    goalie_lines = _dedupe_alumni_regular_season_lines(list(goalie_lines))

    skaters: dict[int, dict[str, object]] = {}
    for line in skater_lines:
        player = line.player
        row = skaters.setdefault(
            int(line.player_id),
            {
                "player": player,
                "years": set(),
                "gp": 0,
                "goals": 0,
                "assists": 0,
                "points": 0,
            },
        )
        years = row["years"]
        if isinstance(years, set):
            years.add(int(line.season_year))
        row["gp"] = int(row["gp"] or 0) + int(line.gp or 0)
        row["goals"] = int(row["goals"] or 0) + int(line.goals or 0)
        row["assists"] = int(row["assists"] or 0) + int(line.assists or 0)
        row["points"] = int(row["points"] or 0) + int(line.goals or 0) + int(line.assists or 0)

    goalies: dict[int, dict[str, object]] = {}
    for line in goalie_lines:
        player = line.player
        row = goalies.setdefault(
            int(line.player_id),
            {
                "player": player,
                "years": set(),
                "gp": 0,
                "wins": 0,
                "shutouts": 0,
            },
        )
        years = row["years"]
        if isinstance(years, set):
            years.add(int(line.season_year))
        row["gp"] = int(row["gp"] or 0) + int(line.gp or 0)
        row["wins"] = int(row["wins"] or 0) + int(line.wins or 0)
        row["shutouts"] = int(row["shutouts"] or 0) + int(line.shutouts or 0)

    out: list[TeamAlumniRow] = []
    for row in skaters.values():
        years = sorted(row["years"]) if isinstance(row["years"], set) else []
        out.append(
            TeamAlumniRow(
                player=row["player"],  # type: ignore[arg-type]
                kind="skater",
                seasons=len(years),
                first_year=years[0] if years else None,
                last_year=years[-1] if years else None,
                gp=int(row["gp"] or 0),
                goals=int(row["goals"] or 0),
                assists=int(row["assists"] or 0),
                points=int(row["points"] or 0),
            )
        )
    for row in goalies.values():
        years = sorted(row["years"]) if isinstance(row["years"], set) else []
        out.append(
            TeamAlumniRow(
                player=row["player"],  # type: ignore[arg-type]
                kind="goalie",
                seasons=len(years),
                first_year=years[0] if years else None,
                last_year=years[-1] if years else None,
                gp=int(row["gp"] or 0),
                wins=int(row["wins"] or 0),
                shutouts=int(row["shutouts"] or 0),
            )
        )

    out.sort(
        key=lambda r: (
            int(r.gp or 0),
            int(r.points or r.wins or 0),
            int(r.last_year or 0),
            (r.player.full_name or "").lower(),
        ),
        reverse=True,
    )
    return out[:limit] if limit is not None else out
