"""Active player injuries imported from FHM player_injuries.csv."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Player, PlayerInjury, Team


def injuries_for_teams(session: Session, team_ids: set[int] | frozenset[int] | None = None) -> list[PlayerInjury]:
    q = (
        select(PlayerInjury)
        .join(PlayerInjury.player)
        .outerjoin(PlayerInjury.team)
        .options(
            joinedload(PlayerInjury.player),
            joinedload(PlayerInjury.team),
            joinedload(PlayerInjury.injury_type),
        )
        .order_by(Team.name, Player.full_name)
    )
    if team_ids is not None:
        q = q.where(PlayerInjury.team_id.in_(team_ids))
    return list(session.scalars(q).all())


def injury_payload_for_team(session: Session, team_id: int) -> list[dict[str, object]]:
    rows = injuries_for_teams(session, {int(team_id)})
    return [_injury_row_dict(row) for row in rows]


def injury_payload_league_wide(session: Session, team_ids: frozenset[int] | None = None) -> list[dict[str, object]]:
    rows = injuries_for_teams(session, team_ids)
    return [_injury_row_dict(row) for row in rows]


def injuries_by_player_id(session: Session, player_ids: set[int]) -> dict[int, dict[str, object]]:
    if not player_ids:
        return {}
    rows = list(
        session.scalars(
            select(PlayerInjury)
            .options(joinedload(PlayerInjury.injury_type))
            .where(PlayerInjury.player_id.in_(player_ids))
        ).all()
    )
    return {int(row.player_id): _injury_row_dict(row) for row in rows}


def _injury_row_dict(row: PlayerInjury) -> dict[str, object]:
    player = row.player
    team = row.team
    it = row.injury_type
    return {
        "player_id": int(row.player_id),
        "player_name": player.full_name if player else "",
        "player_slug": None,
        "team_id": int(row.team_id) if row.team_id else None,
        "team_name": team.full_display_name() if team else "",
        "team_slug": team.slug if team else "",
        "team_abbr": team.abbreviation if team else "",
        "injury_name": it.name if it else "Injury",
        "recovery_days": row.recovery_days,
    }
