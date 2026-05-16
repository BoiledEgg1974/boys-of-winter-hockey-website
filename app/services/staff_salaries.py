"""Staff salary budgets (admin) and default salary formulas (GM page)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Season, Team
from app.services.roster_team import is_main_league_team
from app.services.seasons import get_current_season, season_display_label
from app.site_models import GmLeagueMembership, TeamStaffBudget, User

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class StaffDefaultSalaries:
    head_coach: int
    assistant_coaches: int
    scouts: int
    trainer: int


def gm_display_name(user: User | None) -> str:
    if user is None:
        return "VACANT"
    if user.username and str(user.username).strip():
        return str(user.username).strip().upper()
    if user.discord_name and str(user.discord_name).strip():
        return str(user.discord_name).strip().upper()
    email = str(user.email or "").strip()
    if "@" in email:
        return email.split("@", 1)[0].upper()
    return email.upper() or "VACANT"


def current_season_start_year(session: Session) -> int | None:
    season = get_current_season()
    if season is not None and season.start_year is not None:
        return int(season.start_year)
    season = session.scalar(select(Season).order_by(Season.start_year.desc().nulls_last(), Season.id.desc()).limit(1))
    if season is not None and season.start_year is not None:
        return int(season.start_year)
    return None


def main_league_teams(session: Session) -> list[Team]:
    teams = list(session.scalars(select(Team).order_by(Team.name.asc(), Team.id.asc())).all())
    return [t for t in teams if is_main_league_team(t)]


def budgets_for_season(
    session: Session, *, league_slug: str, season_start_year: int
) -> dict[int, int]:
    rows = session.scalars(
        select(TeamStaffBudget).where(
            TeamStaffBudget.league_slug == league_slug,
            TeamStaffBudget.season_start_year == int(season_start_year),
        )
    ).all()
    return {int(r.team_id): int(r.budget_amount) for r in rows}


def compute_staff_default_salaries(total_budget: int, team_count: int) -> StaffDefaultSalaries | None:
    """Formulas from league rules: average budget per team, divided by 62, then role multipliers."""
    if team_count <= 0 or total_budget <= 0:
        return None
    avg = total_budget / float(team_count)
    base = avg / 62.0
    return StaffDefaultSalaries(
        head_coach=round(base * 7),
        assistant_coaches=round(base * 5),
        scouts=round(base),
        trainer=round(base * 2),
    )


def staff_salary_context(session: Session, *, league_slug: str) -> dict:
    season = get_current_season()
    season_label = season_display_label(season)
    start_year = current_season_start_year(session)
    teams = main_league_teams(session)
    budget_by_team: dict[int, int] = {}
    if start_year is not None:
        budget_by_team = budgets_for_season(session, league_slug=league_slug, season_start_year=start_year)

    active_mems = session.scalars(
        select(GmLeagueMembership).where(
            GmLeagueMembership.league_slug == league_slug,
            GmLeagueMembership.status == "active",
        )
    ).all()
    mem_by_team = {int(m.team_id): m for m in active_mems}
    user_ids = {int(m.user_id) for m in active_mems}
    users_by_id = (
        {int(u.id): u for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()}
        if user_ids
        else {}
    )

    team_rows: list[dict] = []
    total_budget = 0
    for t in teams:
        tid = int(t.id)
        amount = int(budget_by_team.get(tid, 0))
        total_budget += amount
        m = mem_by_team.get(tid)
        u = users_by_id.get(int(m.user_id)) if m else None
        fhm = getattr(t, "fhm_team_id", None)
        team_rows.append(
            {
                "team": t,
                "gm_label": gm_display_name(u),
                "team_id_label": str(fhm).strip() if fhm is not None and str(fhm).strip() else str(tid),
                "budget_amount": amount,
            }
        )

    defaults = compute_staff_default_salaries(total_budget, len(teams))
    return {
        "season": season,
        "season_label": season_label,
        "season_start_year": start_year,
        "team_rows": team_rows,
        "total_budget": total_budget,
        "defaults": defaults,
    }
