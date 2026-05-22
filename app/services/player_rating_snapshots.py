"""Persist point-in-time copies of player_ratings.csv for development trend charts."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Player, PlayerRatingSnapshot, db
from app.services.player_overall_score import compute_player_overall_100, player_is_goalie_for_overall
from app.services.player_rating_avgs import (
    DEF_KEYS,
    GOALIE_KEYS_GOA,
    MENTAL_KEYS_GOALIE,
    MENTAL_KEYS_SKATER,
    OFF_KEYS,
    PHYS_KEYS,
    _float_cell,
)
from app.services.player_ratings_csv import fhm_abi_pot_float, get_player_ratings_row

_log = logging.getLogger(__name__)

GOALIE_ATHLETIC_KEYS: tuple[str, ...] = (
    "g_skating",
    "reflexes",
    "goalie_technique",
    "goalie_overall_positioning",
)
GOALIE_CREASE_KEYS: tuple[str, ...] = (
    "g_positioning",
    "blocker",
    "glove",
    "rebound",
    "recovery",
    "low_shots",
)
GOALIE_PUCK_KEYS: tuple[str, ...] = ("g_passing", "g_pokecheck", "g_puckhandling")

SKATER_SNAPSHOT_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        OFF_KEYS
        + DEF_KEYS
        + MENTAL_KEYS_SKATER
        + PHYS_KEYS
    )
)
GOALIE_SNAPSHOT_KEYS: tuple[str, ...] = tuple(
    dict.fromkeys(
        GOALIE_CREASE_KEYS
        + GOALIE_ATHLETIC_KEYS
        + GOALIE_PUCK_KEYS
        + GOALIE_KEYS_GOA
        + MENTAL_KEYS_GOALIE
    )
)


def tracked_rating_keys(*, is_goalie: bool) -> tuple[str, ...]:
    return GOALIE_SNAPSHOT_KEYS if is_goalie else SKATER_SNAPSHOT_KEYS


def extract_ratings_dict(row: dict[str, Any] | None, *, is_goalie: bool) -> dict[str, float]:
    if not row:
        return {}
    out: dict[str, float] = {}
    for key in tracked_rating_keys(is_goalie=is_goalie):
        val = _float_cell(row.get(key))
        if val is not None:
            out[key] = float(val)
    return out


def _snapshot_payload(
    player: Player,
    ratings_row: dict[str, Any],
    *,
    league_slug: str,
) -> PlayerRatingSnapshot:
    is_g = player_is_goalie_for_overall(player)
    ratings = extract_ratings_dict(ratings_row, is_goalie=is_g)
    abi = fhm_abi_pot_float(ratings_row.get("ability"))
    pot = fhm_abi_pot_float(ratings_row.get("potential"))
    ovr = compute_player_overall_100(abi, pot, ratings_row, is_goalie=is_g)
    return PlayerRatingSnapshot(
        player_id=int(player.id),
        league_slug=league_slug,
        snapshot_at=datetime.utcnow(),
        ratings_json=json.dumps(ratings, sort_keys=True),
        ability=abi,
        potential=pot,
        overall_score=ovr,
    )


def _latest_snapshot(session: Session, player_id: int) -> PlayerRatingSnapshot | None:
    return session.scalars(
        select(PlayerRatingSnapshot)
        .where(PlayerRatingSnapshot.player_id == player_id)
        .order_by(PlayerRatingSnapshot.snapshot_at.desc(), PlayerRatingSnapshot.id.desc())
        .limit(1)
    ).first()


def _snapshot_matches_row(latest: PlayerRatingSnapshot, ratings_row: dict[str, Any], player: Player) -> bool:
    is_g = player_is_goalie_for_overall(player)
    current = extract_ratings_dict(ratings_row, is_goalie=is_g)
    try:
        stored = json.loads(latest.ratings_json or "{}")
    except json.JSONDecodeError:
        return False
    if set(stored.keys()) != set(current.keys()):
        return False
    for key, val in current.items():
        old = stored.get(key)
        if old is None or abs(float(old) - float(val)) > 0.01:
            return False
    abi = fhm_abi_pot_float(ratings_row.get("ability"))
    pot = fhm_abi_pot_float(ratings_row.get("potential"))
    if abi is not None and latest.ability is not None and abs(float(abi) - float(latest.ability)) > 0.01:
        return False
    if pot is not None and latest.potential is not None and abs(float(pot) - float(latest.potential)) > 0.01:
        return False
    return True


def record_player_rating_snapshots_for_league(session: Session, league_slug: str) -> int:
    """Append snapshots for all players with ratings rows (skips unchanged duplicates)."""
    slug = (league_slug or "").strip()
    if not slug:
        return 0
    players = session.scalars(
        select(Player).where(Player.fhm_player_id.isnot(None), Player.fhm_player_id != "")
    ).all()
    added = 0
    for player in players:
        row = get_player_ratings_row(player.fhm_player_id)
        if not row:
            continue
        latest = _latest_snapshot(session, int(player.id))
        if latest is not None and _snapshot_matches_row(latest, row, player):
            continue
        session.add(_snapshot_payload(player, row, league_slug=slug))
        added += 1
    if added:
        session.commit()
        _log.info("Recorded %s player rating snapshots for league %s.", added, slug)
    return added


def seed_player_rating_snapshot_if_needed(
    session: Session,
    player: Player,
    ratings_row: dict[str, Any] | None,
    *,
    league_slug: str,
) -> PlayerRatingSnapshot | None:
    """Insert one current snapshot when a player has ratings but no history yet."""
    if not ratings_row:
        return None
    pid = int(player.id)
    if _latest_snapshot(session, pid) is not None:
        return None
    slug = (league_slug or "").strip()
    if not slug:
        return None
    snap = _snapshot_payload(player, ratings_row, league_slug=slug)
    session.add(snap)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise
    return snap


def load_player_rating_snapshots(
    session: Session,
    player_id: int,
    *,
    within_days: int = 365,
) -> list[PlayerRatingSnapshot]:
    cutoff = datetime.utcnow() - timedelta(days=max(1, within_days))
    rows = session.scalars(
        select(PlayerRatingSnapshot)
        .where(
            PlayerRatingSnapshot.player_id == player_id,
            PlayerRatingSnapshot.snapshot_at >= cutoff,
        )
        .order_by(PlayerRatingSnapshot.snapshot_at.asc(), PlayerRatingSnapshot.id.asc())
    ).all()
    return list(rows)


def record_player_rating_snapshots_after_import(app) -> None:
    """Post-import hook: capture rating snapshots for trend charts."""
    try:
        slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
        if not slug:
            return
        with app.app_context():
            record_player_rating_snapshots_for_league(db.session, slug)
    except Exception:
        _log.exception("player rating snapshot capture failed (non-fatal)")
