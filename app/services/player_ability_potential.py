"""Helpers for ability/potential values stored in FHM player_ratings.csv."""

from __future__ import annotations

from sqlalchemy import or_, select

from app.models import Player
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row


def ability_potential_from_ratings_row(ratings_row: dict | None) -> tuple[float | None, float | None]:
    if not ratings_row:
        return None, None
    return (
        fhm_abi_pot_float(ratings_row.get("ability")),
        fhm_abi_pot_float(ratings_row.get("potential")),
    )


def backfill_missing_ability_potential_from_ratings(session: object) -> int:
    """Fill NULL player ABI/POT from the active league's player_ratings.csv.

    Older imports used plain ``float()`` and dropped Fantasy-style grades like
    ``2.5Bc``. Backfilling the database restores every surface that reads the
    model columns directly.
    """
    players = session.scalars(
        select(Player).where(
            Player.fhm_player_id.isnot(None),
            or_(Player.overall_ability.is_(None), Player.overall_potential.is_(None)),
        )
    ).all()
    changed = 0
    for player in players:
        ratings_row = get_player_ratings_row(player.fhm_player_id)
        ability, potential = ability_potential_from_ratings_row(ratings_row)
        touched = False
        if player.overall_ability is None and ability is not None:
            player.overall_ability = ability
            touched = True
        if player.overall_potential is None and potential is not None:
            player.overall_potential = potential
            touched = True
        if touched:
            changed += 1
    if changed:
        session.commit()
    return changed
