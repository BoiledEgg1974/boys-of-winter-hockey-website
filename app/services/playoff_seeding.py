"""Conference playoff seeding: division winners occupy seeds 1–3 by points."""
from __future__ import annotations

from app.models import TeamStanding

PLAYOFF_DIVISION_WINNER_SEEDING_LEAGUES = frozenset({"bowl-cap", "bowl-fantasy"})


def standing_sort_key(st: TeamStanding) -> tuple[int, int, int, int, int, str]:
    """Sort standings rows the same way playoff projection breaks ties."""
    row = max(int(st.w or 0) - int(st.shootout_wins or 0), 0)
    gd = int(st.gf or 0) - int(st.ga or 0)
    name = ""
    if getattr(st, "team", None) is not None:
        name = (st.team.full_display_name() or "").lower()
    return (
        int(st.pts or 0),
        row,
        gd,
        int(st.gf or 0),
        -int(st.team_id or 0),
        name,
    )


def division_key_for_standing(st: TeamStanding) -> tuple[int, str]:
    team = getattr(st, "team", None)
    if team is not None and team.fhm_division_id is not None:
        return (int(team.fhm_division_id), "")
    return (-1, str(st.division or "").strip().lower())


def order_conference_by_playoff_seeding(rows: list[TeamStanding]) -> list[TeamStanding]:
    """Division winners occupy conference seeds 1–3 (by points among winners); others follow by points."""
    by_division: dict[tuple[int, str], list[TeamStanding]] = {}
    for st in rows:
        by_division.setdefault(division_key_for_standing(st), []).append(st)
    division_winners: list[TeamStanding] = []
    for div_rows in by_division.values():
        if not div_rows:
            continue
        division_winners.append(sorted(div_rows, key=standing_sort_key, reverse=True)[0])
    division_winners.sort(key=standing_sort_key, reverse=True)
    winner_ids = {int(st.team_id) for st in division_winners}
    remaining = [
        st
        for st in sorted(rows, key=standing_sort_key, reverse=True)
        if int(st.team_id) not in winner_ids
    ]
    return division_winners[:3] + remaining


def seed_conference_with_division_winners(rows: list[TeamStanding]) -> list[TeamStanding]:
    """Top-eight playoff seeds for a conference."""
    return order_conference_by_playoff_seeding(rows)[:8]


def league_uses_conference_division_winner_seeding(league_slug: str) -> bool:
    return str(league_slug or "").strip() in PLAYOFF_DIVISION_WINNER_SEEDING_LEAGUES
