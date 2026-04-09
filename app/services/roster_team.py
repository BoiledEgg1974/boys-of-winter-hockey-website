"""Roster/header team: BOWL/NHL club only (FHM league id 0); excludes minors-only assignments."""

from __future__ import annotations

from app.models import Team


def main_league_roster_team(contract_team: Team | None, current_team: Team | None) -> Team | None:
    """Team to show when we only want the main sim league club.

    Players assigned only to national teams, independent/minor-league ids, etc. are treated as
    having no main-roster club so callers can show Minors / RETIRED instead of a non-NHL team.
    Legacy rows with ``fhm_league_id`` NULL are treated as main league.
    """

    def _is_main_league(t: Team | None) -> bool:
        if t is None:
            return False
        lid = t.fhm_league_id
        return lid is None or int(lid) == 0

    if _is_main_league(contract_team):
        return contract_team
    if _is_main_league(current_team):
        return current_team
    return None
