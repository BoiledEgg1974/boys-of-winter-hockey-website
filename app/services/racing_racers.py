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

_COMPACT_RE = re.compile(r"[^a-z0-9]+")


def compact_identity_key(name: str) -> str:
    text = (name or "").strip().lower().replace("$", "s")
    return _COMPACT_RE.sub("", text)


def identity_keys(*names: str) -> set[str]:
    """Comparable tokens for roster names vs Discord / usernames."""
    keys: set[str] = set()
    for raw in names:
        text = str(raw or "").strip()
        if not text or text.lower().startswith("deleted user"):
            continue
        norm = normalize_name_key(text)
        compact = compact_identity_key(text)
        if norm:
            keys.add(norm)
        if compact:
            keys.add(compact)
            stripped = re.sub(r"\d+$", "", compact)
            if stripped:
                keys.add(stripped)
        tokens = re.findall(r"[a-z0-9]+", norm)
        if len(tokens) >= 2:
            keys.add("".join(tokens))
            if len(tokens[-1]) >= 4:
                keys.add(tokens[-1])
    return {k for k in keys if len(k) >= 2}


def _one_edit_apart(left: str, right: str) -> bool:
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    if len(left) > len(right):
        left, right = right, left
    i = 0
    for ch in right:
        if i < len(left) and left[i] == ch:
            i += 1
    return i == len(left)


def identity_match_score(racer_names: list[str], gm_names: list[str]) -> int:
    racer_compact = compact_identity_key(racer_names[0] if racer_names else "")
    gm_compact = compact_identity_key(gm_names[0] if gm_names else "")
    racer_keys = identity_keys(*racer_names)
    gm_keys = identity_keys(*gm_names)
    if not racer_compact or not gm_compact:
        return 0
    if racer_compact == gm_compact:
        return 100
    if racer_keys & gm_keys:
        return 90
    if len(racer_compact) >= 4 and (
        gm_compact.startswith(racer_compact) or gm_compact.endswith(racer_compact)
    ):
        return 80
    if len(racer_compact) >= 3 and gm_compact.startswith(racer_compact):
        return 70
    if min(len(racer_compact), len(gm_compact)) >= 8 and _one_edit_apart(racer_compact, gm_compact):
        return 60
    return 0


def assign_racers_to_gms(
    racers: list[tuple[int, list[str]]],
    gms: list[tuple[int, list[str]]],
) -> dict[int, int]:
    """Map racer id -> GM user id using unique best identity scores."""
    pairs: list[tuple[int, int, int]] = []
    for racer_id, racer_names in racers:
        for gm_id, gm_names in gms:
            score = identity_match_score(racer_names, gm_names)
            if score:
                pairs.append((score, racer_id, gm_id))
    pairs.sort(key=lambda item: (-item[0], item[1], item[2]))
    chosen: dict[int, int] = {}
    used_gms: set[int] = set()
    for _score, racer_id, gm_id in pairs:
        if racer_id in chosen or gm_id in used_gms:
            continue
        chosen[racer_id] = gm_id
        used_gms.add(gm_id)
    return chosen


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
) -> dict[str, object]:
    """Attach Formula roster stubs to Cap / Historical GM site users.

    Matches Discord / usernames onto existing racer names. Does not create extra
    GM-only rows for people who are not on the game roster.
    """
    del include_historical_only  # kept for callers; Hist is used as AP fallback.
    return link_racers_to_gm_profiles(session)


def link_racers_to_gm_profiles(session: Session) -> dict[str, object]:
    """Set ``user_id`` / default Cap (else Historical) AP team on roster racers."""
    racers = list(session.scalars(select(RacingRacer)).all())
    aliases = list(session.scalars(select(RacingNameAlias)).all())
    aliases_by_racer: dict[int, list[str]] = {}
    for alias in aliases:
        aliases_by_racer.setdefault(int(alias.racer_id), []).append(alias.alias)

    users = list(session.scalars(select(User)).all())
    cap_by_user = {
        int(m.user_id): m
        for m in session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == "bowl-cap",
                GmLeagueMembership.status == "active",
            )
        ).all()
    }
    hist_by_user = {
        int(m.user_id): m
        for m in session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == "bowl-historical",
                GmLeagueMembership.status == "active",
            )
        ).all()
    }

    taken_users = {int(r.user_id) for r in racers if r.user_id}
    racer_rows: list[tuple[int, list[str]]] = []
    for racer in racers:
        if racer.user_id:
            continue
        names = [racer.display_name, *aliases_by_racer.get(int(racer.id), [])]
        racer_rows.append((int(racer.id), names))
    gm_rows: list[tuple[int, list[str]]] = []
    for user in users:
        uid = int(user.id)
        if uid in taken_users:
            continue
        display = (user.discord_name or "").strip()
        if display.lower().startswith("deleted user"):
            continue
        gm_rows.append((uid, [display, user.username or ""]))

    chosen = assign_racers_to_gms(racer_rows, gm_rows)
    racers_by_id = {int(r.id): r for r in racers}
    linked: list[str] = []
    for racer_id, user_id in chosen.items():
        racer = racers_by_id[racer_id]
        user = next((u for u in users if int(u.id) == int(user_id)), None)
        racer.user_id = int(user_id)
        racer.is_active = True
        cap = cap_by_user.get(int(user_id))
        hist = hist_by_user.get(int(user_id))
        if cap is not None:
            racer.ap_league_slug = "bowl-cap"
            racer.ap_team_id = int(cap.team_id)
        elif hist is not None:
            racer.ap_league_slug = "bowl-historical"
            racer.ap_team_id = int(hist.team_id)
        if user is not None:
            ensure_alias(session, racer, user.discord_name or "")
            if user.username:
                ensure_alias(session, racer, user.username)
        linked.append(racer.display_name)

    unmatched = [r.display_name for r in racers if not r.user_id]
    return {
        "created": 0,
        "updated": len(linked),
        "skipped": 0,
        "linked": linked,
        "unmatched": unmatched,
    }


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
