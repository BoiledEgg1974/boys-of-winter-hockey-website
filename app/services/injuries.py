"""Active player injuries imported from FHM player_injuries.csv (BOWL-Relegation / FHM12 only)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Player, PlayerInjury, Team

# Only BOWL-Relegation (FHM12) ships injury CSV exports today.
INJURY_LEAGUE_SLUG = "bowl-fantasy"


def injuries_supported_for_league(league_slug: str | None = None) -> bool:
    """True when this league mount should read or display FHM injury data."""
    if league_slug is not None:
        return str(league_slug).strip() == INJURY_LEAGUE_SLUG
    try:
        from flask import has_app_context, current_app

        if has_app_context():
            return str(current_app.config.get("LEAGUE_SLUG") or "").strip() == INJURY_LEAGUE_SLUG
    except RuntimeError:
        pass
    return False


def injuries_for_teams(session: Session, team_ids: set[int] | frozenset[int] | None = None) -> list[PlayerInjury]:
    if not injuries_supported_for_league():
        return []
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
    if not player_ids or not injuries_supported_for_league():
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
