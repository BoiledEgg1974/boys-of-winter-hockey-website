"""Roster/header team: BOWL/NHL club only (FHM league id 0); excludes minors-only assignments."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, Prospect, Team


def is_main_league_team(t: Team | None) -> bool:
    """True for BOWL/NHL clubs (``fhm_league_id`` NULL or 0)."""
    if t is None:
        return False
    lid = t.fhm_league_id
    return lid is None or int(lid) == 0


def main_league_roster_team(contract_team: Team | None, current_team: Team | None) -> Team | None:
    """Team to show when we only want the main sim league club.

    Players assigned only to national teams, independent/minor-league ids, etc. are treated as
    having no main-roster club so callers can show Minors / RETIRED instead of a non-NHL team.
    Legacy rows with ``fhm_league_id`` NULL are treated as main league.
    """

    if is_main_league_team(contract_team):
        return contract_team
    if is_main_league_team(current_team):
        return current_team
    return None


def contract_team_for_player(session: Session, player: Player) -> Team | None:
    """Resolve ``PlayerContract.fhm_team_id`` to a :class:`Team`, if any."""
    c = player.contract
    if c is None or c.fhm_team_id is None:
        return None
    return session.scalars(
        select(Team).where(Team.fhm_team_id == str(c.fhm_team_id)).limit(1)
    ).first()


def organization_main_team(
    session: Session, player: Player, *, prospect: Prospect | None = None
) -> Team | None:
    """BOWL parent club (rights holder), including minors assignments and prospect-only rights rows."""
    ct_team = contract_team_for_player(session, player)
    main = main_league_roster_team(ct_team, player.current_team)
    if main is not None:
        return main
    if prospect is None:
        pr = session.scalar(select(Prospect).where(Prospect.player_id == player.id).limit(1))
    else:
        pr = prospect
    if pr and pr.team_id:
        t = session.get(Team, int(pr.team_id))
        if is_main_league_team(t):
            return t
    return None


def contract_team_from_loaded_maps(player: Player, team_by_fhm_id: dict[str, Team]) -> Team | None:
    """Resolve contract club from preloaded :class:`Team` rows keyed by ``Team.fhm_team_id`` string."""
    c = player.contract
    if c is None or c.fhm_team_id is None:
        return None
    key = str(c.fhm_team_id).strip()
    return team_by_fhm_id.get(key)


def organization_main_team_from_maps(
    player: Player,
    *,
    prospect_by_player_id: dict[int, Prospect | None],
    team_by_id: dict[int, Team],
    team_by_fhm_id: dict[str, Team],
) -> Team | None:
    """Same contract/prospect/main-league logic as :func:`organization_main_team` without per-player SQL."""
    ct_team = contract_team_from_loaded_maps(player, team_by_fhm_id)
    main = main_league_roster_team(ct_team, player.current_team)
    if main is not None:
        return main
    pr = prospect_by_player_id.get(int(player.id))
    if pr and pr.team_id:
        t = team_by_id.get(int(pr.team_id))
        if is_main_league_team(t):
            return t
    return None


def player_exempt_from_expansion_pool(session: Session, player: Player, exempt_team_ids: set[int]) -> bool:
    """True if this player belongs to an exempt BOWL org or is assigned to an exempt team id."""
    if not exempt_team_ids:
        return False
    org = organization_main_team(session, player)
    if org is not None and int(org.id) in exempt_team_ids:
        return True
    if player.current_team_id is not None and int(player.current_team_id) in exempt_team_ids:
        return True
    return False
