"""Cache for Draft Hub eligible prospect pool (shared across web workers via instance/)."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from threading import Lock

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.models import Player
from app.services.draft_hub_eligibility import (
    DraftEligibilityParams,
    board_ranks_map,
    eligible_players_ordered,
)

_CACHE_TTL_SECONDS = 120.0
_lock = Lock()
_pool_cache: dict[tuple, tuple[float, list[int], dict[str, int]]] = {}


def _params_key(params: DraftEligibilityParams) -> tuple:
    return (
        int(params.timeline_year),
        int(params.min_age_years),
        int(params.min_anchor_month),
        int(params.min_anchor_day),
        int(params.max_age_years),
        int(params.max_anchor_month),
        int(params.max_anchor_day),
    )


def _cache_key(league_slug: str, params: DraftEligibilityParams) -> tuple:
    return (str(league_slug).strip(), _params_key(params))


def _cache_dir() -> Path:
    path = BASE_DIR / "instance" / "draft_hub_eligible_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_cache_path(key: tuple) -> Path:
    digest = hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:20]
    slug = str(key[0]).replace("/", "_")
    return _cache_dir() / f"{slug}_{digest}.json"


def _read_file_cache(key: tuple) -> tuple[list[int], dict[str, int]] | None:
    path = _file_cache_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (time.time() - float(data.get("saved_at", 0))) > _CACHE_TTL_SECONDS:
            return None
        ids = [int(x) for x in data.get("ids") or []]
        ranks = {str(k): int(v) for k, v in (data.get("ranks") or {}).items()}
        return ids, ranks
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_file_cache(key: tuple, ids: list[int], ranks: dict[str, int]) -> None:
    path = _file_cache_path(key)
    payload = {"saved_at": time.time(), "ids": ids, "ranks": ranks}
    try:
        path.write_text(json.dumps(payload), encoding="utf-8")
    except OSError:
        pass


def invalidate_eligible_pool_cache(
    *,
    league_slug: str | None = None,
    params: DraftEligibilityParams | None = None,
) -> None:
    """Drop cached pools (e.g. after CSV import). Omit args to clear all."""
    with _lock:
        if league_slug is None and params is None:
            _pool_cache.clear()
        elif params is not None:
            _pool_cache.pop(_cache_key(league_slug or "", params), None)
        else:
            slug = str(league_slug).strip()
            for k in list(_pool_cache.keys()):
                if k[0] == slug:
                    del _pool_cache[k]
    try:
        cache_dir = _cache_dir()
        if league_slug is None and params is None:
            for p in cache_dir.glob("*.json"):
                p.unlink(missing_ok=True)
        elif league_slug:
            prefix = str(league_slug).replace("/", "_") + "_"
            for p in cache_dir.glob(f"{prefix}*.json"):
                p.unlink(missing_ok=True)
    except OSError:
        pass


def eligible_pool_snapshot(
    session: Session,
    league_slug: str,
    params: DraftEligibilityParams,
) -> tuple[list[int], dict[str, int]]:
    """Ordered eligible player ids (full pool) and board rank map; cached per league + rules."""
    key = _cache_key(league_slug, params)
    now = time.monotonic()
    with _lock:
        hit = _pool_cache.get(key)
        if hit and (now - hit[0]) < _CACHE_TTL_SECONDS:
            return hit[1], hit[2]

    file_hit = _read_file_cache(key)
    if file_hit:
        ids, ranks = file_hit
        with _lock:
            _pool_cache[key] = (now, ids, ranks)
        return ids, ranks

    players = eligible_players_ordered(session, league_slug, params)
    ids = [int(p.id) for p in players]
    ranks = board_ranks_map(players)
    _write_file_cache(key, ids, ranks)
    with _lock:
        _pool_cache[key] = (now, ids, ranks)
    return ids, ranks


def eligible_count_for_draft(
    session: Session,
    league_slug: str,
    params: DraftEligibilityParams,
    picked_ids: set[int],
) -> int:
    ids, _ = eligible_pool_snapshot(session, league_slug, params)
    if not picked_ids:
        return len(ids)
    return sum(1 for pid in ids if pid not in picked_ids)


def eligible_id_set_for_draft(
    session: Session,
    league_slug: str,
    params: DraftEligibilityParams,
    picked_ids: set[int],
) -> set[int]:
    ids, _ = eligible_pool_snapshot(session, league_slug, params)
    if not picked_ids:
        return set(ids)
    return {pid for pid in ids if pid not in picked_ids}


def eligible_players_for_board(
    session: Session,
    league_slug: str,
    params: DraftEligibilityParams,
    picked_ids: set[int],
) -> list[Player]:
    """Load Player rows for cached id order, excluding picks in this draft."""
    ids, _ = eligible_pool_snapshot(session, league_slug, params)
    if picked_ids:
        ids = [pid for pid in ids if pid not in picked_ids]
    if not ids:
        return []
    by_id = {
        int(p.id): p
        for p in session.scalars(select(Player).where(Player.id.in_(ids))).unique().all()
    }
    return [by_id[pid] for pid in ids if pid in by_id]
