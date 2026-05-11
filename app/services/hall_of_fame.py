"""Hall of Fame page: plaque rows from ``HallOfFameMember`` + BOWL career and history aggregates."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import HallOfFameMember, Player, PlayerGoalieCareerLine, PlayerSkaterCareerLine, Team
from app.services.player_career_bowl_lines import load_player_bowl_career_table_lines
from app.services.player_career_totals import goalie_career_lines_totals, skater_career_lines_totals
from app.services.player_history_award_badges import (
    player_bowl_cup_win_count,
    player_history_award_badges,
)


def _primary_team_and_last_season_year(
    session: Session,
    lines: list[PlayerSkaterCareerLine] | list[PlayerGoalieCareerLine],
) -> tuple[Team | None, int | None]:
    """Team with the most BOWL RS GP, and last ``season_year`` on that team (for era-correct logos)."""
    gp_by_tid: defaultdict[int, int] = defaultdict(int)
    last_season_by_tid: dict[int, int] = {}
    for ln in lines:
        tid = ln.team_id
        if tid is None:
            t = session.scalars(
                select(Team).where(Team.fhm_team_id == str(ln.team_fhm_id)).limit(1)
            ).first()
            tid = int(t.id) if t else None
        if tid is None:
            continue
        tid_i = int(tid)
        gp_by_tid[tid_i] += int(ln.gp or 0)
        sy = int(ln.season_year)
        prev = last_season_by_tid.get(tid_i)
        if prev is None or sy > prev:
            last_season_by_tid[tid_i] = sy
    if not gp_by_tid:
        return None, None
    best_tid = max(gp_by_tid, key=lambda k: gp_by_tid[k])
    team = session.get(Team, best_tid)
    last_y = last_season_by_tid.get(best_tid)
    return team, (int(last_y) if last_y is not None else None)


def _award_win_total(session: Session, player_id: int) -> int:
    badges = player_history_award_badges(session, player_id)
    return sum(int(b.get("count") or 0) for b in badges)


@dataclass(frozen=True)
class HallOfFamePlaque:
    player: Player
    member_kind: str
    inducted_year: int
    primary_team: Team | None
    #: Start year of last BOWL RS season on ``primary_team`` (for ``team_logo_url_for_season_context``).
    primary_team_logo_season_year: int | None
    award_wins: int
    bowl_cups: int
    skater_totals: dict[str, Any] | None
    goalie_totals: dict[str, Any] | None


def build_hall_of_fame_plaques(session: Session) -> tuple[list[HallOfFamePlaque], list[HallOfFamePlaque]]:
    rows = session.scalars(
        select(HallOfFameMember).options(joinedload(HallOfFameMember.player))
    ).all()
    rows.sort(
        key=lambda r: (
            -int(r.inducted_year),
            int(r.sort_order),
            (r.player.full_name or "").lower(),
        )
    )

    skaters: list[HallOfFamePlaque] = []
    goalies: list[HallOfFamePlaque] = []

    for m in rows:
        pl = m.player
        if not pl:
            continue
        career_rs_sk, _, career_rs_gk, _ = load_player_bowl_career_table_lines(session, int(pl.id))
        if m.member_kind == "goalie":
            primary, logo_y = _primary_team_and_last_season_year(session, career_rs_gk)
            g_tot = goalie_career_lines_totals(career_rs_gk) if career_rs_gk else None
            s_tot = None
            goalies.append(
                HallOfFamePlaque(
                    player=pl,
                    member_kind="goalie",
                    inducted_year=int(m.inducted_year),
                    primary_team=primary,
                    primary_team_logo_season_year=logo_y,
                    award_wins=_award_win_total(session, int(pl.id)),
                    bowl_cups=player_bowl_cup_win_count(session, int(pl.id)),
                    skater_totals=s_tot,
                    goalie_totals=g_tot,
                )
            )
        else:
            primary, logo_y = _primary_team_and_last_season_year(session, career_rs_sk)
            s_tot = skater_career_lines_totals(career_rs_sk) if career_rs_sk else None
            g_tot = None
            skaters.append(
                HallOfFamePlaque(
                    player=pl,
                    member_kind="skater",
                    inducted_year=int(m.inducted_year),
                    primary_team=primary,
                    primary_team_logo_season_year=logo_y,
                    award_wins=_award_win_total(session, int(pl.id)),
                    bowl_cups=player_bowl_cup_win_count(session, int(pl.id)),
                    skater_totals=s_tot,
                    goalie_totals=g_tot,
                )
            )

    return skaters, goalies
