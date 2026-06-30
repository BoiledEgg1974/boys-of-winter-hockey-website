"""Commissioner tools for assigning GM franchises on the hub membership dashboard."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import league_slugs
from app.site_models import GmLeagueMembership, User
from app.services.register_team_options import (
    fhm_team_id_for_league_team,
    team_snapshot_for_membership,
    teams_for_registration,
)


def team_id_valid_for_league(league_slug: str, team_id: int) -> bool:
    if team_id <= 0:
        return False
    return any(int(t["id"]) == int(team_id) for t in teams_for_registration(league_slug))


def admin_assign_gm_franchise(
    session: Session,
    *,
    user_id: int,
    league_slug: str,
    team_id: int,
    replace_existing: bool = False,
) -> tuple[bool, str]:
    """Create or update an active GM franchise assignment. Returns ``(ok, message)``."""
    slug = (league_slug or "").strip()
    if slug not in league_slugs():
        return False, "Choose a valid league."
    if not team_id_valid_for_league(slug, int(team_id)):
        return False, "Choose a valid team for that league."

    user = session.get(User, int(user_id))
    if user is None or user.revoked_at is not None:
        return False, "User not found or login revoked."

    conflict = session.scalar(
        select(GmLeagueMembership)
        .where(
            GmLeagueMembership.league_slug == slug,
            GmLeagueMembership.team_id == int(team_id),
            GmLeagueMembership.status == "active",
            GmLeagueMembership.user_id != int(user_id),
        )
        .limit(1)
    )
    if conflict is not None:
        if not replace_existing:
            other = session.get(User, int(conflict.user_id))
            other_label = (other.email if other else None) or f"user #{conflict.user_id}"
            return (
                False,
                f"That franchise already has an active GM ({other_label}). "
                "Check “Replace existing GM on this team” to remove them first.",
            )
        session.delete(conflict)

    fhm_tid = fhm_team_id_for_league_team(slug, int(team_id))
    membership = session.scalar(
        select(GmLeagueMembership)
        .where(
            GmLeagueMembership.user_id == int(user_id),
            GmLeagueMembership.league_slug == slug,
        )
        .limit(1)
    )
    snap = team_snapshot_for_membership(slug, int(team_id))
    team_label = snap.get("name") or f"team #{team_id}"
    if snap.get("abbr"):
        team_label = f"{team_label} ({snap['abbr']})"

    if membership is None:
        membership = GmLeagueMembership(
            user_id=int(user_id),
            league_slug=slug,
            team_id=int(team_id),
            fhm_team_id=fhm_tid,
            status="active",
            terms_version="v1",
            approved_at=datetime.utcnow(),
        )
        session.add(membership)
        return True, f"Assigned {user.email} to {team_label} ({slug})."

    prev_team_id = int(membership.team_id)
    membership.team_id = int(team_id)
    membership.fhm_team_id = fhm_tid
    membership.status = "active"
    membership.approved_at = datetime.utcnow()
    if prev_team_id == int(team_id):
        return True, f"Confirmed {user.email} on {team_label} ({slug})."
    return True, f"Moved {user.email} to {team_label} ({slug})."
