"""Admin CRUD for Hall of Fame inductees."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import HallOfFameMember, Player

HOF_SOURCE_ADMIN = "admin"
HOF_SOURCE_CSV = "csv"
HOF_MEMBER_KINDS = ("skater", "goalie")


@dataclass(frozen=True)
class PlayerResolveResult:
    player: Player | None
    error: str | None = None


def normalize_hof_member_kind(raw: str) -> str | None:
    kind = (raw or "").strip().lower()
    return kind if kind in HOF_MEMBER_KINDS else None


def resolve_player_for_hof(session: Session, name_or_id: str) -> PlayerResolveResult:
    raw = (name_or_id or "").strip()
    if not raw:
        return PlayerResolveResult(None, "Enter a player name.")
    if raw.isdigit():
        player = session.get(Player, int(raw))
        if player is None:
            player = session.scalar(
                select(Player).where(Player.fhm_player_id == int(raw)).limit(1)
            )
        if player is None:
            return PlayerResolveResult(None, f"No player found for id {raw}.")
        return PlayerResolveResult(player)

    lowered = raw.lower()
    exact = list(
        session.scalars(
            select(Player)
            .where(func.lower(Player.full_name) == lowered)
            .order_by(Player.id.asc())
        ).all()
    )
    if len(exact) == 1:
        return PlayerResolveResult(exact[0])
    if len(exact) > 1:
        names = ", ".join(f"{p.full_name} (id {p.id})" for p in exact[:5])
        return PlayerResolveResult(None, f"Multiple exact players found: {names}. Use the player id.")

    matches = list(
        session.scalars(
            select(Player)
            .where(Player.full_name.ilike(f"%{raw}%"))
            .order_by(Player.full_name.asc(), Player.id.asc())
            .limit(6)
        ).all()
    )
    if len(matches) == 1:
        return PlayerResolveResult(matches[0])
    if matches:
        names = ", ".join(f"{p.full_name} (id {p.id})" for p in matches[:5])
        return PlayerResolveResult(None, f"Multiple player matches found: {names}. Use the exact name or player id.")
    return PlayerResolveResult(None, f"No player found for '{raw}'.")


def list_hof_admin(session: Session) -> list[HallOfFameMember]:
    return list(
        session.scalars(
            select(HallOfFameMember)
            .options(joinedload(HallOfFameMember.player))
            .order_by(
                HallOfFameMember.inducted_year.desc(),
                HallOfFameMember.sort_order.asc(),
                HallOfFameMember.id.desc(),
            )
        ).all()
    )


def player_name_choices(session: Session, *, limit: int = 5000) -> list[str]:
    return list(
        session.scalars(
            select(Player.full_name)
            .where(Player.full_name.is_not(None), Player.full_name != "")
            .order_by(Player.full_name.asc())
            .limit(limit)
        ).all()
    )


def upsert_hof_member(
    session: Session,
    *,
    member_id: int | None,
    player_name: str,
    member_kind: str,
    inducted_year: int,
    user_id: int | None,
) -> tuple[HallOfFameMember | None, str | None]:
    resolved = resolve_player_for_hof(session, player_name)
    if resolved.error:
        return None, resolved.error
    assert resolved.player is not None
    normalized_kind = normalize_hof_member_kind(member_kind)
    if normalized_kind is None:
        return None, "Choose whether this Hall of Fame inductee is a skater or goalie."
    if inducted_year <= 0:
        return None, "Enter a valid induction year."

    duplicate = session.scalar(
        select(HallOfFameMember)
        .where(HallOfFameMember.player_id == int(resolved.player.id))
        .limit(1)
    )
    if duplicate is not None and (member_id is None or int(duplicate.id) != int(member_id)):
        return None, f"{resolved.player.full_name} is already in the Hall of Fame."

    row = session.get(HallOfFameMember, int(member_id)) if member_id else None
    if row is None:
        row = HallOfFameMember(player_id=int(resolved.player.id), member_kind=normalized_kind)
        session.add(row)
    row.player_id = int(resolved.player.id)
    row.member_kind = normalized_kind
    row.inducted_year = int(inducted_year)
    row.source = HOF_SOURCE_ADMIN
    row.updated_at = datetime.utcnow()
    row.updated_by_user_id = user_id
    session.flush()
    return row, None


def delete_hof_member(session: Session, member_id: int) -> bool:
    row = session.get(HallOfFameMember, int(member_id))
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True
