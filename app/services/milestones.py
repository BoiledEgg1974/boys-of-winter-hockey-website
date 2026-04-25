"""Career milestone proximity bundles for skaters and goalies."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import Player, PlayerGoalieCareerLine, PlayerSkaterCareerLine
from app.services.all_time_records import bowl_nhl_league_ids

Split = Literal["rs", "po"]

SKATER_SOURCES: dict[Split, tuple[str, ...]] = {
    "rs": ("rs", "retired_rs"),
    "po": ("po", "retired_po"),
}
GOALIE_SOURCES: dict[Split, tuple[str, ...]] = {
    "rs": ("rs", "retired_rs"),
    "po": ("po", "retired_po"),
}

SKATER_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "gp": (100, 250, 500, 750, 1000, 1250, 1500),
    "goals": (100, 200, 300, 400, 500, 600, 700),
    "assists": (250, 500, 750, 1000),
    "points": (500, 750, 1000, 1250, 1500),
}
GOALIE_THRESHOLDS: dict[str, tuple[int, ...]] = {
    "gp": (100, 250, 500, 750, 1000),
    "wins": (100, 200, 300, 400, 500, 600),
    "shutouts": (25, 50, 75, 100),
}

MAX_MILESTONE_REMAINING = 5


@dataclass
class MilestoneRow:
    player: Player
    current_value: int
    next_milestone: int
    remaining: int


@dataclass
class MilestoneSection:
    key: str
    title: str
    rows: list[MilestoneRow]


def _next_threshold(current_value: int, thresholds: tuple[int, ...]) -> int | None:
    for threshold in thresholds:
        if current_value < threshold:
            return threshold
    return None


def _fetch_skater_totals(session: Session, split: Split) -> list[tuple[Player, int, int, int, int]]:
    line = PlayerSkaterCareerLine
    league_ids = bowl_nhl_league_ids(session)
    sources = SKATER_SOURCES[split]
    sq = (
        select(
            line.player_id.label("pid"),
            func.coalesce(func.sum(line.gp), 0).label("gp"),
            func.coalesce(func.sum(line.goals), 0).label("goals"),
            func.coalesce(func.sum(line.assists), 0).label("assists"),
        )
        .where(line.career_source.in_(sources))
        .where(line.league_fhm_id.in_(league_ids))
        .group_by(line.player_id)
    ).subquery()
    stmt = (
        select(
            Player,
            sq.c.gp,
            sq.c.goals,
            sq.c.assists,
            (sq.c.goals + sq.c.assists).label("points"),
        )
        .join(sq, Player.id == sq.c.pid)
        .options(joinedload(Player.current_team))
        .where(sq.c.gp > 0)
        .where(Player.retired.is_(False))
        .order_by(Player.full_name.asc())
    )
    return list(session.execute(stmt).unique().all())


def _fetch_goalie_totals(session: Session, split: Split) -> list[tuple[Player, int, int, int]]:
    line = PlayerGoalieCareerLine
    league_ids = bowl_nhl_league_ids(session)
    sources = GOALIE_SOURCES[split]
    sq = (
        select(
            line.player_id.label("pid"),
            func.coalesce(func.sum(line.gp), 0).label("gp"),
            func.coalesce(func.sum(line.wins), 0).label("wins"),
            func.coalesce(func.sum(line.shutouts), 0).label("shutouts"),
        )
        .where(line.career_source.in_(sources))
        .where(line.league_fhm_id.in_(league_ids))
        .group_by(line.player_id)
    ).subquery()
    stmt = (
        select(Player, sq.c.gp, sq.c.wins, sq.c.shutouts)
        .join(sq, Player.id == sq.c.pid)
        .options(joinedload(Player.current_team))
        .where(sq.c.gp > 0)
        .where(Player.retired.is_(False))
        .order_by(Player.full_name.asc())
    )
    return list(session.execute(stmt).unique().all())


def _sort_rows(rows: list[MilestoneRow]) -> None:
    rows.sort(key=lambda row: (row.remaining, (row.player.full_name or "").lower(), row.player.id))


def build_milestone_sections(session: Session, split: Split = "rs") -> tuple[list[MilestoneSection], list[MilestoneSection]]:
    """Return skater and goalie milestone sections for the selected split."""
    skater_rows = _fetch_skater_totals(session, split)
    goalie_rows = _fetch_goalie_totals(session, split)

    skater_out: dict[str, list[MilestoneRow]] = {
        "gp": [],
        "goals": [],
        "assists": [],
        "points": [],
    }
    goalie_out: dict[str, list[MilestoneRow]] = {
        "gp": [],
        "wins": [],
        "shutouts": [],
    }

    for player, gp, goals, assists, points in skater_rows:
        for key, value in (
            ("gp", int(gp or 0)),
            ("goals", int(goals or 0)),
            ("assists", int(assists or 0)),
            ("points", int(points or 0)),
        ):
            next_value = _next_threshold(value, SKATER_THRESHOLDS[key])
            if next_value is None:
                continue
            remaining = next_value - value
            if remaining <= 0 or remaining > MAX_MILESTONE_REMAINING:
                continue
            skater_out[key].append(
                MilestoneRow(
                    player=player,
                    current_value=value,
                    next_milestone=next_value,
                    remaining=remaining,
                )
            )

    for player, gp, wins, shutouts in goalie_rows:
        for key, value in (
            ("gp", int(gp or 0)),
            ("wins", int(wins or 0)),
            ("shutouts", int(shutouts or 0)),
        ):
            next_value = _next_threshold(value, GOALIE_THRESHOLDS[key])
            if next_value is None:
                continue
            remaining = next_value - value
            if remaining <= 0 or remaining > MAX_MILESTONE_REMAINING:
                continue
            goalie_out[key].append(
                MilestoneRow(
                    player=player,
                    current_value=value,
                    next_milestone=next_value,
                    remaining=remaining,
                )
            )

    for rows in (*skater_out.values(), *goalie_out.values()):
        _sort_rows(rows)

    skater_sections = [
        MilestoneSection(key="gp", title="Games Played", rows=skater_out["gp"]),
        MilestoneSection(key="goals", title="Goals", rows=skater_out["goals"]),
        MilestoneSection(key="assists", title="Assists", rows=skater_out["assists"]),
        MilestoneSection(key="points", title="Points", rows=skater_out["points"]),
    ]
    goalie_sections = [
        MilestoneSection(key="gp", title="Games Played", rows=goalie_out["gp"]),
        MilestoneSection(key="wins", title="Wins", rows=goalie_out["wins"]),
        MilestoneSection(key="shutouts", title="Shutouts", rows=goalie_out["shutouts"]),
    ]
    return skater_sections, goalie_sections
