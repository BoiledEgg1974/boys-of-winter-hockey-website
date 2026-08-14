"""Durable player Gold/Silver/HoF markers stored in the site DB.

League SQLite ``players.boost_tier`` is a display cache. Site rows survive FHM
imports and ``deploy-db`` league file replacements.
"""
from __future__ import annotations

from datetime import datetime

from flask import current_app, has_app_context
from sqlalchemy import select

from app.league_db import db
from app.models import Player

VALID_BOOST_TIERS = frozenset({"gold", "silver", "hof"})


def normalize_boost_tier(raw: str | None) -> str:
    tier = (raw or "").strip().lower()
    return tier if tier in VALID_BOOST_TIERS else ""


def _league_slug() -> str:
    if not has_app_context():
        return ""
    return str(current_app.config.get("LEAGUE_SLUG") or "").strip()


def _fhm_key(player: Player | None) -> str:
    if player is None:
        return ""
    return str(getattr(player, "fhm_player_id", None) or "").strip()


def _load_site_tier(league_slug: str, fhm_player_id: str) -> str | None:
    """Return stored tier, ``''`` for an explicit clear, or ``None`` if unset."""
    from app.site_models import PlayerBoostMarker

    row = db.session.scalars(
        select(PlayerBoostMarker).where(
            PlayerBoostMarker.league_slug == league_slug,
            PlayerBoostMarker.fhm_player_id == fhm_player_id,
        ).limit(1)
    ).first()
    if row is None:
        return None
    return normalize_boost_tier(row.boost_tier)


def resolved_player_boost_tier(player: Player | None) -> str:
    """Site marker wins; fall back to the league player column."""
    league_val = normalize_boost_tier(getattr(player, "boost_tier", None) if player else None)
    slug = _league_slug()
    fhm = _fhm_key(player)
    if not slug or not fhm:
        return league_val
    try:
        stored = _load_site_tier(slug, fhm)
    except Exception:
        return league_val
    if stored is None:
        return league_val
    return stored


def set_player_boost_tier(
    player: Player,
    tier: str,
    *,
    user_id: int | None = None,
) -> str:
    """Write the marker to the site DB and the league player row."""
    from app.site_models import PlayerBoostMarker

    normalized = normalize_boost_tier(tier)
    player.boost_tier = normalized
    slug = _league_slug()
    fhm = _fhm_key(player)
    if not slug or not fhm:
        return normalized
    row = db.session.scalars(
        select(PlayerBoostMarker).where(
            PlayerBoostMarker.league_slug == slug,
            PlayerBoostMarker.fhm_player_id == fhm,
        ).limit(1)
    ).first()
    now = datetime.utcnow()
    if row is None:
        db.session.add(
            PlayerBoostMarker(
                league_slug=slug,
                fhm_player_id=fhm,
                boost_tier=normalized,
                updated_by_user_id=user_id,
                updated_at=now,
            )
        )
    else:
        row.boost_tier = normalized
        row.updated_by_user_id = user_id
        row.updated_at = now
    return normalized


def sync_player_boost_markers(session=None) -> dict[str, int]:
    """Copy leftover league marks into the site DB, then restore them onto players."""
    sess = session or db.session
    seeded = seed_site_markers_from_league_players(sess)
    applied = apply_site_markers_to_league_players(sess)
    return {"seeded": seeded, "applied": applied}


def seed_site_markers_from_league_players(session=None) -> int:
    """Insert site rows for league marks that are not stored yet."""
    from app.site_models import PlayerBoostMarker

    sess = session or db.session
    slug = _league_slug()
    if not slug:
        return 0
    existing = {
        str(row.fhm_player_id)
        for row in sess.scalars(
            select(PlayerBoostMarker).where(PlayerBoostMarker.league_slug == slug)
        )
    }
    n = 0
    now = datetime.utcnow()
    players = sess.scalars(
        select(Player).where(
            Player.fhm_player_id.is_not(None),
            Player.boost_tier.is_not(None),
        )
    ).all()
    for player in players:
        fhm = _fhm_key(player)
        tier = normalize_boost_tier(player.boost_tier)
        if not fhm or not tier or fhm in existing:
            continue
        sess.add(
            PlayerBoostMarker(
                league_slug=slug,
                fhm_player_id=fhm,
                boost_tier=tier,
                updated_at=now,
            )
        )
        existing.add(fhm)
        n += 1
    return n


def apply_site_markers_to_league_players(session=None) -> int:
    """Copy site markers onto matching league ``players.boost_tier`` rows."""
    from app.site_models import PlayerBoostMarker

    sess = session or db.session
    slug = _league_slug()
    if not slug:
        return 0
    markers = list(
        sess.scalars(select(PlayerBoostMarker).where(PlayerBoostMarker.league_slug == slug))
    )
    if not markers:
        return 0
    by_fhm = {str(m.fhm_player_id): normalize_boost_tier(m.boost_tier) for m in markers}
    players = sess.scalars(
        select(Player).where(Player.fhm_player_id.in_(list(by_fhm.keys())))
    ).all()
    n = 0
    for player in players:
        fhm = _fhm_key(player)
        wanted = by_fhm.get(fhm)
        if wanted is None:
            continue
        current = normalize_boost_tier(player.boost_tier)
        if current != wanted:
            player.boost_tier = wanted
            n += 1
    return n
