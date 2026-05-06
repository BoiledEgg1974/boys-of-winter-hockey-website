"""History awards that are won by a franchise (``team_id`` / notes), not an individual player."""

from __future__ import annotations


def norm_team_award_title(s: str) -> str:
    return " ".join((s or "").upper().split())


# Trophies whose ``history_awards`` row stores the winner on ``team_id`` (not ``player_id``).
TEAM_HISTORY_AWARD_TITLES: frozenset[str] = frozenset(
    (
        "BOILEDEGG'S TROPHY",
        "PRINCE OF WALES TROPHY",
        "CLARENCE CAMPBELL TROPHY",
        "BOWL CUP TROPHY",
    )
)


def is_team_history_award(award_name: str | None) -> bool:
    return norm_team_award_title(award_name or "") in TEAM_HISTORY_AWARD_TITLES
