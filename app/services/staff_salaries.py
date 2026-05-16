"""Staff salary budgets (admin) and default salary formulas (GM page)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Season, Team
from app.services.roster_team import is_main_league_team
from app.services.seasons import get_current_season, season_display_label
from app.services.staff_catalog import BROWSE_FILTERS, STAFF_ROLES, list_staff_for_browse, staff_role_label
from app.services.staff_hire_limits import hire_limit_status
from app.services.staff_images import staff_image_url
from app.services.staff_transactions import (
    active_roster_for_team,
    recent_requests_for_team,
    staff_unavailable_ids,
)
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


def resolve_staff_season(session: Session) -> tuple[Season | None, int | None, str]:
    """Current season for staff budgets/salaries on this league mount only.

    Each site (Historical / Cap / Fantasy) has its own SQLite DB and ``Season`` rows, so
    ``get_current_season()`` resolves that league's timeline independently.
    """
    season = get_current_season()
    if season is None:
        season = session.scalar(
            select(Season).order_by(Season.start_year.desc().nulls_last(), Season.id.desc()).limit(1)
        )
    start_year = int(season.start_year) if season is not None and season.start_year is not None else None
    label = season_display_label(season)
    return season, start_year, label


def current_season_start_year(session: Session) -> int | None:
    _, start_year, _ = resolve_staff_season(session)
    return start_year


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
    season, start_year, season_label = resolve_staff_season(session)
    teams = main_league_teams(session)
    budget_by_team: dict[int, int] = {}
    if start_year is not None:
        budget_by_team = budgets_for_season(
            session,
            league_slug=str(league_slug).strip(),
            season_start_year=int(start_year),
        )

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
    unavailable = staff_unavailable_ids(session, league_slug=league_slug)
    browse_by_filter: dict[str, list[dict]] = {}
    for fk in BROWSE_FILTERS:
        browse_by_filter[fk] = list_staff_for_browse(fk, exclude_staff_ids=unavailable)
    return {
        "league_slug": str(league_slug).strip(),
        "season": season,
        "season_label": season_label,
        "season_start_year": start_year,
        "team_rows": team_rows,
        "total_budget": total_budget,
        "defaults": defaults,
        "staff_roles": STAFF_ROLES,
        "staff_role_labels": {r: staff_role_label(r) for r in STAFF_ROLES},
        "browse_filters": BROWSE_FILTERS,
        "browse_by_filter": browse_by_filter,
    }


def staff_portal_context_for_gm(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    base: dict | None = None,
) -> dict:
    """Extend staff salary page context with hire/fire portal data for one GM team."""
    ctx = dict(base or staff_salary_context(session, league_slug=league_slug))
    start_year = ctx.get("season_start_year")
    if start_year is None:
        ctx["hire_limit"] = None
        ctx["my_roster"] = []
        ctx["recent_requests"] = []
        return ctx
    ctx["hire_limit"] = hire_limit_status(session, league_slug=league_slug, team_id=team_id)
    roster_entries = active_roster_for_team(
        session, league_slug=league_slug, team_id=team_id, season_start_year=int(start_year)
    )
    ctx["my_roster"] = [
        {
            "entry": e,
            "role_label": staff_role_label(e.role),
            "image_url": staff_image_url(league_slug, e.staff_fhm_id),
        }
        for e in roster_entries
    ]
    ctx["recent_requests"] = recent_requests_for_team(
        session, league_slug=league_slug, team_id=team_id, limit=8
    )
    for fk in BROWSE_FILTERS:
        for ent in ctx.get("browse_by_filter", {}).get(fk, []):
            ent["image_url"] = staff_image_url(league_slug, ent.get("staff_fhm_id"))
    return ctx
