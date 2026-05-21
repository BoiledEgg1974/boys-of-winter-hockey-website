"""Staff hire/fire requests: submit, approve, deny, roster updates."""
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
from app.services.staff_hire_limits import hire_limit_status
from app.site_models import StaffChangeRequest, TeamStaffRosterEntry


@dataclass(frozen=True)
class StaffRequestResult:
    ok: bool
    message: str
    request: StaffChangeRequest | None = None


def _active_roster_entry(
    session: Session, *, league_slug: str, staff_fhm_id: str
) -> TeamStaffRosterEntry | None:
    return session.scalar(
        select(TeamStaffRosterEntry)
        .where(
            TeamStaffRosterEntry.league_slug == league_slug,
            TeamStaffRosterEntry.staff_fhm_id == staff_fhm_id,
            TeamStaffRosterEntry.fired_at.is_(None),
        )
        .limit(1)
    )


def staff_unavailable_ids(session: Session, *, league_slug: str) -> set[str]:
    """Staff already on a roster or with a pending hire in this league."""
    out: set[str] = set()
    for row in session.scalars(
        select(TeamStaffRosterEntry.staff_fhm_id).where(
            TeamStaffRosterEntry.league_slug == league_slug,
            TeamStaffRosterEntry.fired_at.is_(None),
        )
    ).all():
        out.add(str(row).strip())
    for row in session.scalars(
        select(StaffChangeRequest.staff_fhm_id).where(
            StaffChangeRequest.league_slug == league_slug,
            StaffChangeRequest.request_type == "hire",
            StaffChangeRequest.status == "pending",
        )
    ).all():
        out.add(str(row).strip())
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
    """Ensure site roster rows exist for staff assigned to this team in FHM CSVs."""
    profiles = list_staff_profiles_for_fhm_team(fhm_team_id)
    if not profiles:
        return 0
    active = active_roster_for_team(
        session,
        league_slug=league_slug,
        team_id=int(team_id),
        season_start_year=int(season_start_year),
    )
    on_team = {str(e.staff_fhm_id).strip() for e in active}
    added = 0
    now = datetime.utcnow()
    for prof in profiles:
        sid = str(prof.get("staff_fhm_id") or "").strip()
        if not sid or sid in on_team:
            continue
        other = _active_roster_entry(session, league_slug=league_slug, staff_fhm_id=sid)
        if other is not None and int(other.team_id) != int(team_id):
            continue
        session.add(
            TeamStaffRosterEntry(
                league_slug=league_slug,
                season_start_year=int(season_start_year),
                team_id=int(team_id),
                staff_fhm_id=sid,
                staff_name=str(prof.get("full_name") or "—"),
                role=_infer_staff_role_for_team(profiles, prof),
                hire_request_id=None,
                hired_at=now,
            )
        )
        on_team.add(sid)
        added += 1
    if added:
        session.flush()
    return added


def pending_fire_staff_ids(
    session: Session, *, league_slug: str, team_id: int
) -> set[str]:
    rows = session.scalars(
        select(StaffChangeRequest.staff_fhm_id).where(
            StaffChangeRequest.league_slug == league_slug,
            StaffChangeRequest.team_id == int(team_id),
            StaffChangeRequest.request_type == "fire",
            StaffChangeRequest.status == "pending",
        )
    ).all()
    return {str(r).strip() for r in rows if str(r).strip()}


def active_roster_for_team(
    session: Session, *, league_slug: str, team_id: int, season_start_year: int
) -> list[TeamStaffRosterEntry]:
    return list(
        session.scalars(
            select(TeamStaffRosterEntry)
            .where(
                TeamStaffRosterEntry.league_slug == league_slug,
                TeamStaffRosterEntry.team_id == int(team_id),
                TeamStaffRosterEntry.season_start_year == int(season_start_year),
                TeamStaffRosterEntry.fired_at.is_(None),
            )
            .order_by(TeamStaffRosterEntry.role.asc(), TeamStaffRosterEntry.staff_name.asc())
        ).all()
    )


def recent_requests_for_team(
    session: Session, *, league_slug: str, team_id: int, limit: int = 8
) -> list[StaffChangeRequest]:
    return list(
        session.scalars(
            select(StaffChangeRequest)
            .where(
                StaffChangeRequest.league_slug == league_slug,
                StaffChangeRequest.team_id == int(team_id),
            )
            .order_by(StaffChangeRequest.created_at.desc(), StaffChangeRequest.id.desc())
            .limit(limit)
        ).all()
    )


def submit_hire_request(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    user_id: int,
    staff_fhm_id: str,
    role: str,
) -> StaffRequestResult:
    role_s = str(role or "").strip()
    if role_s not in STAFF_ROLES:
        return StaffRequestResult(False, "Invalid staff role.")
    sid = str(staff_fhm_id or "").strip()
    prof = get_staff_profile(sid)
    if prof is None:
        return StaffRequestResult(False, "Staff member not found in league catalog.")
    if is_staff_assigned_to_any_fhm_team(prof):
        return StaffRequestResult(
            False, "That staff member is already under contract with another team."
        )
    lim = hire_limit_status(session, league_slug=league_slug, team_id=team_id)
    if lim.limit_reached:
        return StaffRequestResult(
            False,
            f"Daily hire limit reached ({lim.used}/{lim.limit} for {lim.window_label}).",
        )
    if _active_roster_entry(session, league_slug=league_slug, staff_fhm_id=sid):
        return StaffRequestResult(False, "That staff member is already employed in this league.")
    pending_hire = session.scalar(
        select(StaffChangeRequest)
        .where(
            StaffChangeRequest.league_slug == league_slug,
            StaffChangeRequest.staff_fhm_id == sid,
            StaffChangeRequest.request_type == "hire",
            StaffChangeRequest.status == "pending",
        )
        .limit(1)
    )
    if pending_hire:
        return StaffRequestResult(False, "A pending hire request already exists for this staff member.")
    req = StaffChangeRequest(
        league_slug=league_slug,
        season_start_year=int(season_start_year),
        team_id=int(team_id),
        user_id=int(user_id),
        request_type="hire",
        role=role_s,
        staff_fhm_id=sid,
        staff_name=str(prof.get("full_name") or "—"),
        status="pending",
    )
    session.add(req)
    session.flush()
    return StaffRequestResult(True, "Hire request submitted for admin approval.", request=req)


def submit_fire_request(
    session: Session,
    *,
    league_slug: str,
    season_start_year: int,
    team_id: int,
    user_id: int,
    roster_entry_id: int,
) -> StaffRequestResult:
    entry = session.get(TeamStaffRosterEntry, int(roster_entry_id))
    if (
        entry is None
        or entry.league_slug != league_slug
        or int(entry.team_id) != int(team_id)
        or entry.fired_at is not None
    ):
        return StaffRequestResult(False, "Staff member not on your active roster.")
    pending = session.scalar(
        select(StaffChangeRequest)
        .where(
            StaffChangeRequest.league_slug == league_slug,
            StaffChangeRequest.staff_fhm_id == entry.staff_fhm_id,
            StaffChangeRequest.request_type == "fire",
            StaffChangeRequest.status == "pending",
        )
        .limit(1)
    )
    if pending:
        return StaffRequestResult(False, "A pending fire request already exists for this staff member.")
    req = StaffChangeRequest(
        league_slug=league_slug,
        season_start_year=int(season_start_year),
        team_id=int(team_id),
        user_id=int(user_id),
        request_type="fire",
        role=entry.role,
        staff_fhm_id=entry.staff_fhm_id,
        staff_name=entry.staff_name,
        status="pending",
    )
    session.add(req)
    session.flush()
    return StaffRequestResult(True, "Fire request submitted for admin approval.", request=req)


def _deny_other_pending_hires(session: Session, *, league_slug: str, staff_fhm_id: str, except_id: int) -> int:
    rows = session.scalars(
        select(StaffChangeRequest).where(
            StaffChangeRequest.league_slug == league_slug,
            StaffChangeRequest.staff_fhm_id == staff_fhm_id,
            StaffChangeRequest.request_type == "hire",
            StaffChangeRequest.status == "pending",
            StaffChangeRequest.id != int(except_id),
        )
    ).all()
    now = datetime.utcnow()
    for row in rows:
        row.status = "denied"
        row.processed_at = now
        row.admin_note = "Auto-denied: another team hire was approved for this staff member."
    return len(rows)


def approve_staff_request(
    session: Session,
    req: StaffChangeRequest,
    *,
    admin_user_id: int,
) -> StaffRequestResult:
    if req.status != "pending":
        return StaffRequestResult(False, "Request is not pending.")
    now = datetime.utcnow()
    slug = req.league_slug
    if req.request_type == "hire":
        if _active_roster_entry(session, league_slug=slug, staff_fhm_id=req.staff_fhm_id):
            req.status = "denied"
            req.processed_at = now
            req.processed_by_user_id = admin_user_id
            req.admin_note = "Denied: staff member already employed in this league."
            return StaffRequestResult(False, req.admin_note)
        role_s = str(req.role or "head_coach")
        session.add(
            TeamStaffRosterEntry(
                league_slug=slug,
                season_start_year=int(req.season_start_year),
                team_id=int(req.team_id),
                staff_fhm_id=req.staff_fhm_id,
                staff_name=req.staff_name,
                role=role_s,
                hire_request_id=int(req.id),
                hired_at=now,
            )
        )
        req.status = "approved"
        req.processed_at = now
        req.processed_by_user_id = admin_user_id
        _deny_other_pending_hires(session, league_slug=slug, staff_fhm_id=req.staff_fhm_id, except_id=req.id)
        return StaffRequestResult(True, "Hire approved.", request=req)
    if req.request_type == "fire":
        entry = session.scalar(
            select(TeamStaffRosterEntry)
            .where(
                TeamStaffRosterEntry.league_slug == slug,
                TeamStaffRosterEntry.team_id == int(req.team_id),
                TeamStaffRosterEntry.staff_fhm_id == req.staff_fhm_id,
                TeamStaffRosterEntry.fired_at.is_(None),
            )
            .limit(1)
        )
        if entry is None:
            req.status = "denied"
            req.processed_at = now
            req.processed_by_user_id = admin_user_id
            req.admin_note = "Denied: staff member is not on this team's active roster."
            return StaffRequestResult(False, req.admin_note)
        entry.fired_at = now
        req.status = "approved"
        req.processed_at = now
        req.processed_by_user_id = admin_user_id
        return StaffRequestResult(True, "Fire approved.", request=req)
    return StaffRequestResult(False, "Unknown request type.")


def deny_staff_request(
    session: Session,
    req: StaffChangeRequest,
    *,
    admin_user_id: int,
    admin_note: str = "",
) -> StaffRequestResult:
    if req.status != "pending":
        return StaffRequestResult(False, "Request is not pending.")
    req.status = "denied"
    req.processed_at = datetime.utcnow()
    req.processed_by_user_id = admin_user_id
    req.admin_note = (admin_note or "").strip()
    return StaffRequestResult(True, "Request denied.", request=req)


def transaction_headline(req: StaffChangeRequest, team: Team | None) -> str:
    team_label = team.full_display_name() if team else f"Team {req.team_id}"
    role_l = staff_role_label(req.role)
    if req.request_type == "hire":
        return f"Staff hired — {req.staff_name} ({role_l}) — {team_label}"
    return f"Staff fired — {req.staff_name} ({role_l}) — {team_label}"
