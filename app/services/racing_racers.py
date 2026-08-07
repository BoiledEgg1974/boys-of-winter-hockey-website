"""Racer roster sync and CSV name resolution for racing mounts."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.racing_models import RacingNameAlias, RacingRacer
from app.services.racing_csv import normalize_name_key
from app.site_models import GmLeagueMembership, User


def resolve_racer_by_name(session: Session, name: str) -> RacingRacer | None:
    key = normalize_name_key(name)
    if not key:
        return None
    alias = session.scalar(select(RacingNameAlias).where(RacingNameAlias.alias_key == key).limit(1))
    if alias is not None:
        return session.get(RacingRacer, int(alias.racer_id))
    return session.scalar(
        select(RacingRacer).where(RacingRacer.display_name == name.strip()).limit(1)
    )


def ensure_alias(session: Session, racer: RacingRacer, alias: str) -> RacingNameAlias | None:
    key = normalize_name_key(alias)
    if not key:
        return None
    existing = session.scalar(select(RacingNameAlias).where(RacingNameAlias.alias_key == key).limit(1))
    if existing is not None:
        if int(existing.racer_id) != int(racer.id):
            return None
        return existing
    row = RacingNameAlias(racer_id=int(racer.id), alias=alias.strip(), alias_key=key)
    session.add(row)
    return row


def list_racers(session: Session, *, active_only: bool = True) -> list[RacingRacer]:
    q = select(RacingRacer).order_by(RacingRacer.display_name.asc())
    if active_only:
        q = q.where(RacingRacer.is_active.is_(True))
    return list(session.scalars(q).all())


def sync_racers_from_cap(
    session: Session,
    *,
    include_historical_only: bool = True,
) -> dict[str, int]:
    """Seed/update racers from active Cap GMs; optionally add Historical-only GMs.

    No duplicate ``user_id``. Existing racers keep their display name / AP link
    unless newly created.
    """
    created = 0
    updated = 0
    skipped = 0

    cap_memberships = list(
        session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == "bowl-cap",
                GmLeagueMembership.status == "active",
            )
        ).all()
    )
    hist_memberships = list(
        session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == "bowl-historical",
                GmLeagueMembership.status == "active",
            )
        ).all()
    )
    hist_by_user = {int(m.user_id): m for m in hist_memberships}
    cap_user_ids = {int(m.user_id) for m in cap_memberships}

    user_ids = {int(m.user_id) for m in cap_memberships}
    if include_historical_only:
        user_ids |= {uid for uid in hist_by_user if uid not in cap_user_ids}
    users = {
        int(u.id): u
        for u in session.scalars(select(User).where(User.id.in_(user_ids))).all()
    } if user_ids else {}

    def _display_for(user: User | None, membership: GmLeagueMembership) -> str:
        from app.services.gm_messaging import gm_display_name

        if user is not None:
            name = gm_display_name(user)
            if name and name != "—":
                return name
        return f"GM #{int(membership.user_id)}"

    # Cap GMs first
    for m in cap_memberships:
        user = users.get(int(m.user_id))
        existing = session.scalar(
            select(RacingRacer).where(RacingRacer.user_id == int(m.user_id)).limit(1)
        )
        display = _display_for(user, m)
        if existing is None:
            # Avoid unique display_name collisions
            base = display
            suffix = 2
            while session.scalar(
                select(RacingRacer.id).where(RacingRacer.display_name == display).limit(1)
            ):
                display = f"{base} ({suffix})"
                suffix += 1
            racer = RacingRacer(
                display_name=display,
                user_id=int(m.user_id),
                ap_league_slug="bowl-cap",
                ap_team_id=int(m.team_id),
                is_active=True,
            )
            session.add(racer)
            session.flush()
            ensure_alias(session, racer, display)
            created += 1
        else:
            existing.ap_league_slug = existing.ap_league_slug or "bowl-cap"
            existing.ap_team_id = existing.ap_team_id or int(m.team_id)
            existing.is_active = True
            ensure_alias(session, existing, existing.display_name)
            updated += 1

    if include_historical_only:
        for uid, m in hist_by_user.items():
            if uid in cap_user_ids:
                continue
            existing = session.scalar(
                select(RacingRacer).where(RacingRacer.user_id == uid).limit(1)
            )
            if existing is not None:
                skipped += 1
                continue
            user = users.get(uid)
            display = _display_for(user, m)
            base = display
            suffix = 2
            while session.scalar(
                select(RacingRacer.id).where(RacingRacer.display_name == display).limit(1)
            ):
                display = f"{base} ({suffix})"
                suffix += 1
            racer = RacingRacer(
                display_name=display,
                user_id=uid,
                ap_league_slug="bowl-historical",
                ap_team_id=int(m.team_id),
                is_active=True,
            )
            session.add(racer)
            session.flush()
            ensure_alias(session, racer, display)
            created += 1

    return {"created": created, "updated": updated, "skipped": skipped}


def set_racer_ap_target(
    session: Session,
    racer: RacingRacer,
    *,
    ap_league_slug: str,
    ap_team_id: int,
) -> None:
    if ap_league_slug not in ("bowl-cap", "bowl-historical"):
        raise ValueError("AP target must be bowl-cap or bowl-historical")
    racer.ap_league_slug = ap_league_slug
    racer.ap_team_id = int(ap_team_id)


def add_manual_alias(session: Session, racer_id: int, alias: str) -> RacingNameAlias:
    racer = session.get(RacingRacer, int(racer_id))
    if racer is None:
        raise ValueError("Racer not found")
    row = ensure_alias(session, racer, alias)
    if row is None:
        raise ValueError("Alias already mapped to another racer")
    return row
