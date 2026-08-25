"""Racer roster sync and CSV name resolution for racing mounts."""
from __future__ import annotations

import os
import re
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.racing_models import (
    RacingApSuggestion,
    RacingChannelCredit,
    RacingCircuitStanding,
    RacingEventResult,
    RacingNameAlias,
    RacingRacer,
)
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


DEFAULT_ROSTER_TXT_PATHS: dict[str, tuple[str, ...]] = {
    "bowl-formula": (
        r"C:\Users\keeno\Projects\Formula BOWL\game\data\roster.txt",
        r"C:\Users\keeno\OneDrive\Desktop\Formula BOWL\game\data\roster.txt",
    ),
    "bowl-demolition": (
        r"C:\Users\keeno\Projects\BOWL Demotion Derby\names\roster.txt",
        r"C:\Users\keeno\OneDrive\Desktop\BOWL Demotion Derby\names\roster.txt",
    ),
}


def default_roster_txt_path(league_slug: str) -> Path:
    slug = str(league_slug or "").strip()
    env_key = f"BOWL_RACING_ROSTER_{slug.replace('-', '_').upper()}"
    env_val = (os.environ.get(env_key) or "").strip().strip('"')
    candidates: list[Path] = []
    if env_val:
        candidates.append(Path(env_val).expanduser())
    defaults = DEFAULT_ROSTER_TXT_PATHS.get(slug) or ()
    candidates.extend(Path(p).expanduser() for p in defaults)
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0] if candidates else Path()


def parse_roster_txt(path: Path) -> list[dict[str, object]]:
    """Parse Formula (``N|Name``) or Demolition (one name per line) roster.txt files."""
    text = path.read_text(encoding="utf-8", errors="replace")
    out: list[dict[str, object]] = []
    auto_num = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        car_number: int | None = None
        name = line
        m = re.match(r"^(\d+)\s*[|:]\s*(.+)$", line)
        if m:
            car_number = int(m.group(1))
            name = m.group(2).strip()
        else:
            m2 = re.match(r"^(.+?)\s*[|:]\s*#?(\d+)\s*$", line)
            if m2:
                name = m2.group(1).strip()
                car_number = int(m2.group(2))
        name = name.strip()
        if not name:
            continue
        if car_number is None:
            auto_num += 1
            car_number = auto_num
        out.append({"name": name, "car_number": car_number})
    return out


def _find_racer_for_roster_name(session: Session, name: str) -> RacingRacer | None:
    key = normalize_name_key(name)
    if not key:
        return None
    hit = resolve_racer_by_name(session, name)
    if hit is not None:
        return hit
    # Case/spacing-insensitive match against display names.
    for racer in session.scalars(select(RacingRacer)).all():
        if normalize_name_key(racer.display_name) == key:
            return racer
    return None


def delete_racer(session: Session, racer_id: int) -> str:
    """Remove a racer and aliases. Race rows keep the printed driver name."""
    racer = session.get(RacingRacer, int(racer_id))
    if racer is None:
        raise ValueError("Racer not found")
    name = str(racer.display_name)
    rid = int(racer.id)
    for alias in list(
        session.scalars(select(RacingNameAlias).where(RacingNameAlias.racer_id == rid)).all()
    ):
        session.delete(alias)
    for model, column in (
        (RacingEventResult, RacingEventResult.racer_id),
        (RacingCircuitStanding, RacingCircuitStanding.racer_id),
        (RacingApSuggestion, RacingApSuggestion.racer_id),
        (RacingChannelCredit, RacingChannelCredit.racer_id),
    ):
        for row in session.scalars(select(model).where(column == rid)).all():
            row.racer_id = None
    session.delete(racer)
    session.flush()
    return name


def _racer_name_keys(racer: RacingRacer) -> set[str]:
    keys = {normalize_name_key(racer.display_name)}
    for alias in racer.aliases:
        keys.add(str(alias.alias_key))
    keys.discard("")
    return keys


def prune_stub_racers_not_in_keys(session: Session, keep_keys: set[str]) -> list[str]:
    """Delete roster stubs (no site user) whose names are not in ``keep_keys``.

    Cap/Historical GM racers (``user_id`` set) are left alone.
    """
    keep = {k for k in keep_keys if k}
    removed: list[str] = []
    stubs = list(session.scalars(select(RacingRacer).where(RacingRacer.user_id.is_(None))).all())
    for racer in stubs:
        if _racer_name_keys(racer) & keep:
            continue
        removed.append(delete_racer(session, int(racer.id)))
    return removed


def link_roster_txt(
    session: Session,
    path: Path,
    *,
    create_unmatched: bool = True,
    prune_missing: bool = True,
) -> dict[str, object]:
    """Link game roster.txt names onto RacingRacer rows (aliases + optional stubs).

    Does not overwrite existing ``user_id`` / AP targets. New stubs are created
    without Cap/Hist links so admins can attach them later. When ``prune_missing``
    is set, leftover stubs (e.g. Kings after a rename to GreedyFish) are deleted.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Roster file not found: {path}")
    entries = parse_roster_txt(path)
    linked = 0
    aliased = 0
    created = 0
    conflicts: list[str] = []
    unmatched: list[str] = []

    for entry in entries:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        racer = _find_racer_for_roster_name(session, name)
        if racer is None:
            if not create_unmatched:
                unmatched.append(name)
                continue
            display = name
            base = display
            suffix = 2
            while session.scalar(
                select(RacingRacer.id).where(RacingRacer.display_name == display).limit(1)
            ):
                display = f"{base} ({suffix})"
                suffix += 1
            racer = RacingRacer(
                display_name=display,
                user_id=None,
                ap_league_slug=None,
                ap_team_id=None,
                is_active=True,
                notes="Created from game roster.txt",
            )
            session.add(racer)
            session.flush()
            created += 1
        else:
            linked += 1
        row = ensure_alias(session, racer, name)
        if row is None:
            conflicts.append(name)
        else:
            aliased += 1
        # Keep primary display alias as well.
        ensure_alias(session, racer, racer.display_name)

    pruned: list[str] = []
    if prune_missing:
        keep_keys = {normalize_name_key(str(e.get("name") or "")) for e in entries}
        pruned = prune_stub_racers_not_in_keys(session, keep_keys)

    return {
        "path": str(path),
        "entries": len(entries),
        "linked": linked,
        "aliased": aliased,
        "created": created,
        "conflicts": conflicts,
        "unmatched": unmatched,
        "pruned": pruned,
    }
