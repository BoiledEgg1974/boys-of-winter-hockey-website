"""Admin CRUD for Hall of Fame inductees."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models import HallOfFameMember, Player

HOF_SOURCE_ADMIN = "admin"
HOF_SOURCE_CSV = "csv"
HOF_MEMBER_KINDS = ("skater", "goalie")
_TRAILING_PLAYER_ID = re.compile(r"^(.*?)(?:\s+#(\d+))?\s*$")


@dataclass(frozen=True)
class PlayerResolveResult:
    player: Player | None
    error: str | None = None


def normalize_hof_member_kind(raw: str) -> str | None:
    kind = (raw or "").strip().lower()
    return kind if kind in HOF_MEMBER_KINDS else None


def normalize_hof_player_query(raw: str) -> tuple[str, int | None]:
    """Strip decorative ids (``Glenn Resch #10952``) while preserving numeric ids."""
    text = (raw or "").strip()
    if not text:
        return "", None
    if text.isdigit():
        return text, int(text)
    match = _TRAILING_PLAYER_ID.match(text)
    if not match:
        return text, None
    name = (match.group(1) or "").strip()
    trailing_id = match.group(2)
    if trailing_id:
        return name or text, int(trailing_id)
    return text, None


def resolve_player_for_hof(
    session: Session,
    name_or_id: str,
    *,
    player_id: int | None = None,
) -> PlayerResolveResult:
    if player_id is not None:
        player = session.get(Player, int(player_id))
        if player is None:
            return PlayerResolveResult(None, f"No player found for id {player_id}.")
        return PlayerResolveResult(player)

    raw, trailing_id = normalize_hof_player_query(name_or_id)
    if trailing_id is not None and not raw:
        player = session.get(Player, trailing_id)
        if player is None:
            return PlayerResolveResult(None, f"No player found for id {trailing_id}.")
        return PlayerResolveResult(player)

    if not raw:
        return PlayerResolveResult(None, "Enter a player name.")
    if raw.isdigit():
        pid = int(raw)
        player = session.get(Player, pid)
        if player is None:
            player = session.scalar(
                select(Player).where(Player.fhm_player_id == str(pid)).limit(1)
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

    words = [part for part in re.split(r"\s+", raw) if part]
    if words:
        word_query = select(Player)
        for word in words:
            word_query = word_query.where(Player.full_name.ilike(f"%{word}%"))
        word_matches = list(
            session.scalars(
                word_query.order_by(Player.full_name.asc(), Player.id.asc()).limit(6)
            ).all()
        )
        if len(word_matches) == 1:
            return PlayerResolveResult(word_matches[0])
        if word_matches:
            names = ", ".join(f"{p.full_name} (id {p.id})" for p in word_matches[:5])
            return PlayerResolveResult(
                None,
                f"Multiple player matches found: {names}. Pick a suggestion or enter the player id.",
            )

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


def upsert_hof_member(
    session: Session,
    *,
    member_id: int | None,
    player_name: str,
    player_id: int | None = None,
    member_kind: str,
    inducted_year: int,
    user_id: int | None,
) -> tuple[HallOfFameMember | None, str | None]:
    resolved = resolve_player_for_hof(session, player_name, player_id=player_id)
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
