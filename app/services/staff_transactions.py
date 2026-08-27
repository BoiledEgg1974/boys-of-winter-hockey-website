"""Staff hire/fire: admin league office actions and roster contract management."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Team
from app.services.staff_catalog import (
    STAFF_ROLES,
    get_staff_profile,
    is_staff_assigned_to_any_fhm_team,
    list_staff_profiles_for_fhm_team,
    staff_role_label,
)
from app.site_models import StaffChangeRequest, StaffSeveranceEntry, TeamStaffBudget, TeamStaffRosterEntry


@dataclass(frozen=True)
class StaffActionResult:
    ok: bool
    message: str
    entry: TeamStaffRosterEntry | None = None


def contract_end_season_year(entry: TeamStaffRosterEntry) -> int:
    start = int(entry.contract_start_season_year or 0)
    years = int(entry.contract_years or 1)
    if start <= 0:
        return 0
    return start + max(1, years) - 1


def contract_years_remaining(entry: TeamStaffRosterEntry, season_start_year: int) -> int:
    """Years left on the term, counting the current season."""
    end = contract_end_season_year(entry)
    years = max(1, int(entry.contract_years or 1))
    if end <= 0:
        return years if int(entry.annual_salary or 0) > 0 else 0
    return max(0, end - int(season_start_year) + 1)


def contract_active(entry: TeamStaffRosterEntry, season_start_year: int) -> bool:
    if entry.fired_at is not None or entry.retired_at is not None:
        return False
    end = contract_end_season_year(entry)
    if end <= 0:
        return int(entry.annual_salary or 0) > 0
    return int(season_start_year) <= end


def _entry_claims_staff(entry: TeamStaffRosterEntry, season_start_year: int) -> bool:
    """True when a roster row should block hiring/contracting elsewhere."""
    if not contract_active(entry, int(season_start_year)):
        return False
    return int(entry.annual_salary or 0) > 0 or int(entry.contract_start_season_year or 0) > 0


def _open_roster_entries_for_staff(
    session: Session, *, league_slug: str, staff_fhm_id: str
) -> list[TeamStaffRosterEntry]:
    return list(
        session.scalars(
            select(TeamStaffRosterEntry).where(
                TeamStaffRosterEntry.league_slug == league_slug,
                TeamStaffRosterEntry.staff_fhm_id == staff_fhm_id,
                TeamStaffRosterEntry.fired_at.is_(None),
                TeamStaffRosterEntry.retired_at.is_(None),
            )
        ).all()
    )


def _open_roster_entries_for_team(
    session: Session, *, league_slug: str, team_id: int
) -> list[TeamStaffRosterEntry]:
    return list(
        session.scalars(
            select(TeamStaffRosterEntry)
            .where(
                TeamStaffRosterEntry.league_slug == league_slug,
                TeamStaffRosterEntry.team_id == int(team_id),
                TeamStaffRosterEntry.fired_at.is_(None),
                TeamStaffRosterEntry.retired_at.is_(None),
            )
            .order_by(TeamStaffRosterEntry.role.asc(), TeamStaffRosterEntry.staff_name.asc())
        ).all()
    )


def _pick_best_roster_row(
    rows: list[TeamStaffRosterEntry], season_start_year: int
) -> TeamStaffRosterEntry | None:
    """Prefer an active claim, then a current-season draft, then the newest row."""
    if not rows:
        return None
    year = int(season_start_year)

    def _sort_key(row: TeamStaffRosterEntry) -> tuple[int, int, int, int, int]:
        claims = 1 if _entry_claims_staff(row, year) else 0
        active = 1 if contract_active(row, year) else 0
        current_snap = 1 if int(row.season_start_year or 0) == year else 0
        start = int(row.contract_start_season_year or row.season_start_year or 0)
        rid = int(row.id or 0)
        return (claims, active, current_snap, start, rid)

    return max(rows, key=_sort_key)


def _find_team_roster_entry(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    staff_fhm_id: str,
    season_start_year: int,
) -> TeamStaffRosterEntry | None:
    """Open roster row for this staff on this team, including prior-season active terms."""
    rows = [
        row
        for row in _open_roster_entries_for_staff(
            session, league_slug=league_slug, staff_fhm_id=staff_fhm_id
        )
        if int(row.team_id) == int(team_id)
    ]
    return _pick_best_roster_row(rows, int(season_start_year))


def _active_roster_entry(
    session: Session,
    *,
    league_slug: str,
    staff_fhm_id: str,
    season_start_year: int | None = None,
) -> TeamStaffRosterEntry | None:
    """Return a real active contract claim for this staff, if any.

    Empty placeholder rows (no salary / no contract start) and expired terms do
    not count — those ghosts used to block Staff-tab salary saves forever.
    """
    rows = _open_roster_entries_for_staff(
        session, league_slug=league_slug, staff_fhm_id=staff_fhm_id
    )
    if season_start_year is None:
        return rows[0] if rows else None
    for row in rows:
        if _entry_claims_staff(row, int(season_start_year)):
            return row
    return None


def _release_roster_entries(
    session: Session,
    entries: list[TeamStaffRosterEntry],
    *,
    except_team_id: int | None = None,
) -> int:
    """Mark roster rows fired so they no longer block other teams."""
    now = datetime.utcnow()
    released = 0
    for row in entries:
        if except_team_id is not None and int(row.team_id) == int(except_team_id):
            continue
        if row.fired_at is not None:
            continue
        row.fired_at = now
        released += 1
    if released:
        session.flush()
    return released


def staff_unavailable_ids(
    session: Session, *, league_slug: str, season_start_year: int | None = None
) -> set[str]:
    """Staff with an active league contract."""
    out: set[str] = set()
    for row in session.scalars(
        select(TeamStaffRosterEntry).where(
            TeamStaffRosterEntry.league_slug == league_slug,
            TeamStaffRosterEntry.fired_at.is_(None),
            TeamStaffRosterEntry.retired_at.is_(None),
        )
    ).all():
        if season_start_year is not None:
            if _entry_claims_staff(row, int(season_start_year)):
                out.add(str(row.staff_fhm_id).strip())
            continue
        if int(row.annual_salary or 0) > 0 or int(row.contract_start_season_year or 0) > 0:
            out.add(str(row.staff_fhm_id).strip())
    return out


def _infer_staff_role_for_team(profiles: list[dict], profile: dict) -> str:
    """Map FHM staff_master assignment to site roster role keys."""
    bucket = str(profile.get("primary_bucket") or "coaches")
    if bucket == "scouts":
        return "scout"
    if bucket == "trainers":
        return "trainer"
    coaches = [p for p in profiles if str(p.get("primary_bucket") or "") == "coaches"]
    if len(coaches) <= 1:
        return "head_coach"
    head = max(
        coaches,
        key=lambda p: (
            float(p.get("coach_rating") or -1),
            str(p.get("full_name") or "").lower(),
        ),
    )
    return "head_coach" if head is profile else "assistant_coach"


def sync_team_roster_from_fhm(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    season_start_year: int,
    fhm_team_id: str | int | None,
) -> int:
    """No-op: portal contracts are created via admin hire or team Staff tab."""
    return 0


def active_roster_for_team(
    session: Session, *, league_slug: str, team_id: int, season_start_year: int
) -> list[TeamStaffRosterEntry]:
    """Active contracts for payroll, including remaining terms from prior seasons."""
    year = int(season_start_year)
    rows = _open_roster_entries_for_team(
        session, league_slug=league_slug, team_id=int(team_id)
    )
    grouped: dict[str, list[TeamStaffRosterEntry]] = {}
    for row in rows:
        sid = str(row.staff_fhm_id).strip()
        if sid:
            grouped.setdefault(sid, []).append(row)
    out: list[TeamStaffRosterEntry] = []
    for sid_rows in grouped.values():
        best = _pick_best_roster_row(sid_rows, year)
        if best is not None and contract_active(best, year):
            out.append(best)
    out.sort(key=lambda r: (str(r.role or ""), str(r.staff_name or "")))
    return out


def staff_contracts_for_team(
    session: Session,
    *,
    league_slug: str,
    team_id: int,
    season_start_year: int,
) -> dict[str, TeamStaffRosterEntry]:
    """Active remaining contracts and current-season drafts, keyed by staff_fhm_id.

    Rows are matched by active term, not only ``season_start_year`` snapshot, so
    multi-year deals still show after a league year change.
    """
    year = int(season_start_year)
    rows = _open_roster_entries_for_team(
        session, league_slug=league_slug, team_id=int(team_id)
    )
    grouped: dict[str, list[TeamStaffRosterEntry]] = {}
    for row in rows:
        sid = str(row.staff_fhm_id).strip()
        if sid:
            grouped.setdefault(sid, []).append(row)
    out: dict[str, TeamStaffRosterEntry] = {}
    for sid, sid_rows in grouped.items():
        best = _pick_best_roster_row(sid_rows, year)
        if best is None:
            continue
        if contract_active(best, year) or int(best.season_start_year or 0) == year:
            out[sid] = best
    return out


def expire_stale_staff_contracts(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
) -> int:
    """Mark expired contracts as fired so they drop from payroll and public lists.

    Scans all open rows for the league (not only the current season_start_year
    snapshot) so prior-season leftovers cannot keep blocking Staff-tab saves.
    """
    now = datetime.utcnow()
    rows = session.scalars(
        select(TeamStaffRosterEntry).where(
            TeamStaffRosterEntry.league_slug == league_slug,
            TeamStaffRosterEntry.fired_at.is_(None),
            TeamStaffRosterEntry.retired_at.is_(None),
        )
    ).all()
    expired = 0
    for row in rows:
        if not contract_active(row, int(season_start_year)):
            row.fired_at = now
            expired += 1
    if expired:
        session.flush()
    return expired


def backfill_staff_contract_fields(session: Session) -> int:
    """One-time backfill for roster rows missing contract data."""
    from app.services.league_finances import default_salary_for_role

    updated = 0
    rows = session.scalars(select(TeamStaffRosterEntry)).all()
    leagues_seasons: dict[tuple[str, int], object] = {}
    for row in rows:
        if row.fired_at is not None or row.retired_at is not None:
            continue
        needs = (
            int(row.annual_salary or 0) <= 0
            or int(row.contract_start_season_year or 0) <= 0
            or int(row.contract_years or 0) <= 0
        )
        if not needs:
            continue
        key = (str(row.league_slug), int(row.season_start_year))
        if key not in leagues_seasons:
            leagues_seasons[key] = _league_staff_defaults(
                session,
                league_slug=key[0],
                season_start_year=key[1],
            )
        defaults = leagues_seasons[key]
        if int(row.annual_salary or 0) <= 0:
            row.annual_salary = int(default_salary_for_role(str(row.role), defaults))
        if int(row.contract_years or 0) <= 0:
            row.contract_years = 1
        if int(row.contract_start_season_year or 0) <= 0:
            row.contract_start_season_year = int(row.season_start_year)
        updated += 1
    if updated:
        session.flush()
    return updated


def _league_staff_defaults(
    session: Session, *, league_slug: str, season_start_year: int
):
    from app.services.staff_salaries import (
        compute_staff_default_salaries,
        main_league_teams,
        staff_budget_data_for_season,
    )

    teams = main_league_teams(session)
    budget_data = staff_budget_data_for_season(
        session,
        league_slug=league_slug,
        season_start_year=int(season_start_year),
    )
    total_budget = sum(int(data["budget_amount"]) for data in budget_data.values())
    return compute_staff_default_salaries(total_budget, len(teams))


def _get_or_create_team_staff_budget(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
) -> TeamStaffBudget:
    row = session.scalar(
        select(TeamStaffBudget).where(
            TeamStaffBudget.league_slug == league_slug,
            TeamStaffBudget.season_start_year == int(season_start_year),
            TeamStaffBudget.team_id == int(team_id),
        ).limit(1)
    )
    if row is not None:
        return row
    row = TeamStaffBudget(
        league_slug=league_slug,
        season_start_year=int(season_start_year),
        team_id=int(team_id),
        budget_amount=0,
        current_salary_amount=0,
    )
    session.add(row)
    session.flush()
    return row


def _validate_hire_budget(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    role: str,
    annual_salary: int,
) -> str | None:
    from app.services.league_finances import STAFF_HIRE_INSUFFICIENT_FUNDS_MSG, can_afford_staff_hire, staff_finances_for_team

    finances = staff_finances_for_team(
        session,
        league_slug=league_slug,
        team_id=int(team_id),
        season_start_year=int(season_start_year),
    )
    available = int(finances.get("available_for_hire", 0))
    if int(annual_salary) > available:
        return STAFF_HIRE_INSUFFICIENT_FUNDS_MSG
    budget_amount = int(finances.get("budget_amount", 0))
    staff_payroll = int(finances.get("staff_payroll", 0))
    if budget_amount > 0 and staff_payroll + int(annual_salary) > budget_amount:
        return STAFF_HIRE_INSUFFICIENT_FUNDS_MSG
    return None


def admin_hire_staff(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    admin_user_id: int,
    staff_fhm_id: str,
    role: str,
    contract_years: int,
    annual_salary: int | None = None,
) -> StaffActionResult:
    role_s = str(role or "").strip()
    if role_s not in STAFF_ROLES:
        return StaffActionResult(False, "Invalid staff role.")
    years = max(1, min(10, int(contract_years or 1)))
    sid = str(staff_fhm_id or "").strip()
    prof = get_staff_profile(sid)
    if prof is None:
        return StaffActionResult(False, "Staff member not found in league catalog.")
    if is_staff_assigned_to_any_fhm_team(prof):
        return StaffActionResult(
            False, "That staff member is already under contract in Franchise Hockey Manager."
        )
    if _active_roster_entry(
        session,
        league_slug=league_slug,
        staff_fhm_id=sid,
        season_start_year=int(season_start_year),
    ):
        return StaffActionResult(False, "That staff member already has an active league contract.")
    defaults = _league_staff_defaults(
        session,
        league_slug=league_slug,
        season_start_year=int(season_start_year),
    )
    from app.services.league_finances import default_salary_for_role

    salary = int(annual_salary) if annual_salary is not None else int(default_salary_for_role(role_s, defaults))
    if role_s == "team_owner":
        salary = 0
    if salary < 0:
        return StaffActionResult(False, "Contract salary cannot be negative.")
    err = _validate_hire_budget(
        session,
        league_slug=league_slug,
        season_start_year=int(season_start_year),
        team_id=int(team_id),
        role=role_s,
        annual_salary=salary,
    )
    if err:
        return StaffActionResult(False, err)
    now = datetime.utcnow()
    entry = TeamStaffRosterEntry(
        league_slug=league_slug,
        season_start_year=int(season_start_year),
        team_id=int(team_id),
        staff_fhm_id=sid,
        staff_name=str(prof.get("full_name") or "—"),
        role=role_s,
        hire_request_id=None,
        hired_at=now,
        annual_salary=salary,
        contract_years=years,
        contract_start_season_year=int(season_start_year),
    )
    session.add(entry)
    session.flush()
    return StaffActionResult(True, f"Hired {entry.staff_name} ({staff_role_label(role_s)}).", entry=entry)


def admin_fire_staff(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    admin_user_id: int,
    staff_fhm_id: str,
    penalty_amount: int = 0,
) -> StaffActionResult:
    sid = str(staff_fhm_id or "").strip()
    entry = _find_team_roster_entry(
        session,
        league_slug=league_slug,
        team_id=int(team_id),
        staff_fhm_id=sid,
        season_start_year=int(season_start_year),
    )
    if entry is None or not contract_active(entry, int(season_start_year)):
        return StaffActionResult(False, "Staff member not on this team's active roster.")
    now = datetime.utcnow()
    entry.fired_at = now
    penalty = max(0, int(penalty_amount or 0))
    if penalty > 0:
        session.add(
            StaffSeveranceEntry(
                league_slug=league_slug,
                season_start_year=int(season_start_year),
                team_id=int(team_id),
                staff_fhm_id=sid,
                staff_name=str(entry.staff_name or ""),
                penalty_amount=penalty,
                created_by_user_id=int(admin_user_id),
            )
        )
    session.flush()
    return StaffActionResult(
        True,
        f"Fired {entry.staff_name}." + (f" Penalty ${penalty:,} recorded." if penalty else ""),
        entry=entry,
    )


MANUAL_SEVERANCE_STAFF_ID = "__manual__"
MANUAL_SEVERANCE_STAFF_NAME = "Manual salary penalty"


def admin_set_team_staff_penalty_total(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    penalty_amount: int,
    admin_user_id: int,
) -> int:
    """Set total staff salary penalties for a team/season.

    Fire-linked severance rows are kept when the new total is at or above their
    sum; any extra is stored as a single manual adjustment. Reducing below the
    fire-linked total replaces those rows with one consolidated entry.
    """
    desired = max(0, int(penalty_amount or 0))
    existing = list(
        session.scalars(
            select(StaffSeveranceEntry).where(
                StaffSeveranceEntry.league_slug == league_slug,
                StaffSeveranceEntry.team_id == int(team_id),
                StaffSeveranceEntry.season_start_year == int(season_start_year),
            )
        ).all()
    )
    manuals = [e for e in existing if str(e.staff_fhm_id or "") == MANUAL_SEVERANCE_STAFF_ID]
    fire_rows = [e for e in existing if str(e.staff_fhm_id or "") != MANUAL_SEVERANCE_STAFF_ID]
    fire_total = sum(int(e.penalty_amount or 0) for e in fire_rows)
    current = fire_total + sum(int(e.penalty_amount or 0) for e in manuals)
    if current == desired:
        return desired

    for row in manuals:
        session.delete(row)

    if desired > fire_total:
        extra = desired - fire_total
        session.add(
            StaffSeveranceEntry(
                league_slug=league_slug,
                season_start_year=int(season_start_year),
                team_id=int(team_id),
                staff_fhm_id=MANUAL_SEVERANCE_STAFF_ID,
                staff_name=MANUAL_SEVERANCE_STAFF_NAME,
                penalty_amount=extra,
                created_by_user_id=int(admin_user_id),
            )
        )
    elif desired < fire_total:
        for row in fire_rows:
            session.delete(row)
        if desired > 0:
            session.add(
                StaffSeveranceEntry(
                    league_slug=league_slug,
                    season_start_year=int(season_start_year),
                    team_id=int(team_id),
                    staff_fhm_id=MANUAL_SEVERANCE_STAFF_ID,
                    staff_name=MANUAL_SEVERANCE_STAFF_NAME,
                    penalty_amount=desired,
                    created_by_user_id=int(admin_user_id),
                )
            )
    # desired == fire_total: manuals already removed; fire rows remain
    session.flush()
    return desired


def admin_retire_staff(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    staff_fhm_id: str,
) -> StaffActionResult:
    sid = str(staff_fhm_id or "").strip()
    entry = _find_team_roster_entry(
        session,
        league_slug=league_slug,
        team_id=int(team_id),
        staff_fhm_id=sid,
        season_start_year=int(season_start_year),
    )
    if entry is None:
        return StaffActionResult(False, "Staff member not found on this team's roster.")
    now = datetime.utcnow()
    entry.retired_at = now
    session.flush()
    return StaffActionResult(True, f"Retired {entry.staff_name}.", entry=entry)


def admin_save_staff_contract(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    staff_fhm_id: str,
    role: str,
    annual_salary: int,
    contract_years: int,
    contract_start_season_year: int | None = None,
    fhm_team_id: str | int | None = None,
) -> StaffActionResult:
    role_s = str(role or "").strip()
    if role_s not in STAFF_ROLES:
        return StaffActionResult(False, "Invalid staff role.")
    sid = str(staff_fhm_id or "").strip()
    prof = get_staff_profile(sid)
    if prof is None:
        return StaffActionResult(False, "Staff member not found in league catalog.")
    salary = max(0, int(annual_salary or 0))
    if role_s == "team_owner":
        salary = 0
    years = max(1, min(10, int(contract_years or 1)))
    now = datetime.utcnow()
    open_rows = _open_roster_entries_for_staff(
        session, league_slug=league_slug, staff_fhm_id=sid
    )
    team_rows = [row for row in open_rows if int(row.team_id) == int(team_id)]
    entry = _pick_best_roster_row(team_rows, int(season_start_year))
    if entry is None:
        start_year = int(contract_start_season_year or season_start_year)
        other = next(
            (
                row
                for row in open_rows
                if int(row.team_id) != int(team_id)
                and _entry_claims_staff(row, int(season_start_year))
            ),
            None,
        )
        staff_fhm_tid = str(prof.get("fhm_team_id") or "").strip()
        this_fhm_tid = str(fhm_team_id or "").strip()
        # Staff tab lists current FHM assignments. If they belong here now, any
        # prior portal contract on another club is an invisible orphan — release it.
        belongs_here = bool(this_fhm_tid and staff_fhm_tid == this_fhm_tid)
        if other is not None and not belongs_here:
            return StaffActionResult(False, "That staff member is contracted to another team.")
        if open_rows:
            _release_roster_entries(
                session, open_rows, except_team_id=int(team_id)
            )
        profiles = list_staff_profiles_for_fhm_team(fhm_team_id) if fhm_team_id else []
        inferred = _infer_staff_role_for_team(profiles, prof) if profiles else role_s
        entry = TeamStaffRosterEntry(
            league_slug=league_slug,
            season_start_year=int(season_start_year),
            team_id=int(team_id),
            staff_fhm_id=sid,
            staff_name=str(prof.get("full_name") or "—"),
            role=role_s if role_s else inferred,
            hire_request_id=None,
            hired_at=now,
            annual_salary=salary,
            contract_years=years,
            contract_start_season_year=start_year,
        )
        session.add(entry)
    else:
        if contract_start_season_year is not None:
            start_year = int(contract_start_season_year)
        else:
            start_year = int(entry.contract_start_season_year or season_start_year)
        if open_rows:
            _release_roster_entries(
                session, open_rows, except_team_id=int(team_id)
            )
        entry.role = role_s
        entry.annual_salary = salary
        entry.contract_years = years
        entry.contract_start_season_year = start_year
        entry.season_start_year = int(season_start_year)
    if salary > 0:
        roster = active_roster_for_team(
            session,
            league_slug=league_slug,
            team_id=int(team_id),
            season_start_year=int(season_start_year),
        )
        from app.services.league_finances import contract_roster_payroll, severance_payroll

        existing_salary = 0
        if entry.id is not None:
            for r in roster:
                if str(r.staff_fhm_id).strip() == sid:
                    existing_salary = int(r.annual_salary or 0)
                    break
        projected = (
            contract_roster_payroll(roster, int(season_start_year))
            - existing_salary
            + salary
            + severance_payroll(
                session,
                league_slug=league_slug,
                team_id=int(team_id),
                season_start_year=int(season_start_year),
            )
        )
        budget_row = _get_or_create_team_staff_budget(
            session,
            league_slug=league_slug,
            season_start_year=int(season_start_year),
            team_id=int(team_id),
        )
        if int(budget_row.budget_amount or 0) > 0 and projected > int(budget_row.budget_amount):
            return StaffActionResult(False, "Team staff budget would be exceeded.")
    session.flush()
    return StaffActionResult(True, f"Contract saved for {entry.staff_name}.", entry=entry)


def transaction_headline_for_entry(entry: TeamStaffRosterEntry, team: Team | None, *, action: str) -> str:
    team_label = team.full_display_name() if team else f"Team {entry.team_id}"
    role_l = staff_role_label(entry.role)
    if action == "hire":
        return f"Staff hired — {entry.staff_name} ({role_l}) — {team_label}"
    if action == "retire":
        return f"Staff retired — {entry.staff_name} ({role_l}) — {team_label}"
    return f"Staff fired — {entry.staff_name} ({role_l}) — {team_label}"


# Legacy aliases kept for historical request rows / tests
StaffRequestResult = StaffActionResult


def transaction_headline(req: StaffChangeRequest, team: Team | None) -> str:
    team_label = team.full_display_name() if team else f"Team {req.team_id}"
    role_l = staff_role_label(req.role)
    if req.request_type == "hire":
        return f"Staff hired — {req.staff_name} ({role_l}) — {team_label}"
    return f"Staff fired — {req.staff_name} ({role_l}) — {team_label}"
