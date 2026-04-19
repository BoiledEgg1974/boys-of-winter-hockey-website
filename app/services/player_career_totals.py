"""Aggregate career stat lines for player profile tables.

Callers should pass only the lines to sum (e.g. BOWL/NHL rows filtered by ``league_fhm_id``).
"""

from __future__ import annotations

from typing import Any

from app.models import PlayerGoalieCareerLine, PlayerSkaterCareerLine


def skater_career_lines_totals(lines: list[PlayerSkaterCareerLine]) -> dict[str, Any]:
    """Sums counting stats; optional columns use '—' semantics when entirely absent."""
    gp = sum(ln.gp for ln in lines)
    goals = sum(ln.goals for ln in lines)
    assists = sum(ln.assists for ln in lines)
    points = goals + assists
    pim = sum(ln.pim for ln in lines)

    pm_has = any(ln.plus_minus is not None for ln in lines)
    plus_minus = (
        sum(ln.plus_minus for ln in lines if ln.plus_minus is not None) if pm_has else None
    )

    def _sum_optional(attr: str) -> int | None:
        if not any(getattr(ln, attr) is not None for ln in lines):
            return None
        return sum(getattr(ln, attr) or 0 for ln in lines)

    return {
        "gp": gp,
        "goals": goals,
        "assists": assists,
        "points": points,
        "plus_minus": plus_minus,
        "pim": pim,
        "pp_goals": _sum_optional("pp_goals"),
        "sh_goals": _sum_optional("sh_goals"),
        "shots": _sum_optional("shots"),
    }


def goalie_career_lines_totals(lines: list[PlayerGoalieCareerLine]) -> dict[str, Any]:
    """Career goalie totals; GAA and SV% from combined minutes-style aggregates (GP/GA/SA)."""
    gp = sum(ln.gp for ln in lines)
    wins = sum(ln.wins for ln in lines)
    losses = sum(ln.losses for ln in lines)
    shutouts = sum(ln.shutouts for ln in lines)
    goals_against = sum(ln.goals_against for ln in lines)
    shots_against = sum(ln.shots_against for ln in lines)

    otl_all_missing = all(ln.ties_otl is None for ln in lines)
    ties_otl = None if otl_all_missing else sum(ln.ties_otl or 0 for ln in lines)

    gaa = (goals_against / gp) if gp else None
    sv_pct = ((shots_against - goals_against) / shots_against) if shots_against else None

    return {
        "gp": gp,
        "wins": wins,
        "losses": losses,
        "ties_otl": ties_otl,
        "gaa": gaa,
        "sv_pct": sv_pct,
        "shutouts": shutouts,
    }
