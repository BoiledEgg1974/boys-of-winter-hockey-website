"""File-backed JSON cache shared across uWSGI workers (Historical, Cap, Fantasy).

Uses *stale-while-revalidate*: serve the last good payload immediately (even past
the fresh TTL), then refresh in a background thread so users are not blocked on
cold rebuilds.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from flask import Flask, current_app

from app.config import BASE_DIR
from app.league_urls import league_test_request_context, prefix_league_static_urls, real_flask_app
from app.services.layout_nav_cache import league_engine_sqlite_fingerprint

_log = logging.getLogger(__name__)


_lock = threading.Lock()
_mem_cache: dict[tuple, tuple[float, float, dict[str, Any]]] = {}
_compute_locks: dict[str, threading.Lock] = {}
_refresh_inflight: set[str] = set()

DEFAULT_FRESH_TTL_SECONDS: dict[str, float] = {
    "homepage_summary": 120.0,
    "playoff_bracket": 300.0,
    "game_boxscore_final": 3600.0,
    "game_boxscore_live": 45.0,
    "game_preview_final": 3600.0,
    "game_preview_live": 60.0,
    "player_hover": 180.0,
    "team_hover": 180.0,
    "search_players": 45.0,
    "postseason_odds": 600.0,
}

DEFAULT_STALE_TTL_SECONDS: dict[str, float] = {
    "homepage_summary": 7200.0,
    "playoff_bracket": 7200.0,
    "game_boxscore_final": 86400.0,
    "game_boxscore_live": 600.0,
    "game_preview_final": 86400.0,
    "game_preview_live": 3600.0,
    "player_hover": 7200.0,
    "team_hover": 7200.0,
    "search_players": 600.0,
    "postseason_odds": 7200.0,
}

# Back-compat alias for callers using a single TTL.
DEFAULT_TTL_SECONDS = DEFAULT_FRESH_TTL_SECONDS


@dataclass(frozen=True)
class CacheEntry:
    body: dict[str, Any]
    saved_at: float
    is_fresh: bool


def _cache_root() -> Path:
    path = BASE_DIR / "instance" / "league_json_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def site_db_fingerprint(app: Flask) -> str:
    from app.models import db

    try:
        engine = db.get_engine(app, bind="site")
    except Exception:
        return "nosite"
    fp = league_engine_sqlite_fingerprint(engine)
    if fp is not None:
        return f"site:{fp}"
    return "site:mem"


def league_db_fingerprint(app: Flask) -> str:
    from app.models import db

    fp = league_engine_sqlite_fingerprint(db.engine)
    if fp is not None:
        return f"league:{fp}"
    fp_slug = str(app.config.get("LEAGUE_SLUG") or "")
    return f"league:mem:{fp_slug}"


def cache_key(
    namespace: str,
    key_suffix: tuple,
    *,
    app: Flask | None = None,
) -> tuple:
    app = app or current_app
    league_slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
    return (
        str(namespace).strip(),
        league_slug,
        league_db_fingerprint(app),
        site_db_fingerprint(app),
        *key_suffix,
    )


def _digest(key: tuple) -> str:
    return hashlib.sha256(repr(key).encode("utf-8")).hexdigest()[:24]


def _file_path(key: tuple) -> Path:
    slug = str(key[1]).replace("/", "_")
    namespace = str(key[0]).replace("/", "_")
    return _cache_root() / f"{slug}__{namespace}__{_digest(key)}.json"


def _entry_from_saved(saved_at: float, body: dict[str, Any], *, fresh_ttl: float, stale_ttl: float) -> CacheEntry | None:
    age = time.time() - saved_at
    if age > stale_ttl:
        return None
    return CacheEntry(body=body, saved_at=saved_at, is_fresh=age <= fresh_ttl)


def _read_file_entry(key: tuple, *, fresh_ttl: float, stale_ttl: float) -> CacheEntry | None:
    path = _file_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") != list(key):
            return None
        body = data.get("body")
        if not isinstance(body, dict):
            return None
        saved_at = float(data.get("saved_at", 0))
        return _entry_from_saved(saved_at, body, fresh_ttl=fresh_ttl, stale_ttl=stale_ttl)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_file(key: tuple, body: dict[str, Any]) -> float:
    saved_at = time.time()
    path = _file_path(key)
    payload = {"saved_at": saved_at, "key": list(key), "body": body}
    try:
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass
    return saved_at


def get_cache_entry(
    namespace: str,
    key_suffix: tuple,
    *,
    fresh_ttl: float,
    stale_ttl: float,
    app: Flask | None = None,
) -> CacheEntry | None:
    key = cache_key(namespace, key_suffix, app=app)
    now = time.time()
    with _lock:
        hit = _mem_cache.get(key)
        if hit is not None:
            saved_at, _stored_at, body = hit
            ent = _entry_from_saved(saved_at, body, fresh_ttl=fresh_ttl, stale_ttl=stale_ttl)
            if ent is not None:
                return ent

    file_ent = _read_file_entry(key, fresh_ttl=fresh_ttl, stale_ttl=stale_ttl)
    if file_ent is not None:
        with _lock:
            _mem_cache[key] = (file_ent.saved_at, now, file_ent.body)
        return file_ent
    return None


def get_cached_json(
    namespace: str,
    key_suffix: tuple,
    ttl_seconds: float,
    *,
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    stale_ttl_seconds: float | None = None,
) -> dict[str, Any] | None:
    stale = float(stale_ttl_seconds if stale_ttl_seconds is not None else max(ttl_seconds * 30, ttl_seconds + 120))
    ent = get_cache_entry(namespace, key_suffix, fresh_ttl=ttl_seconds, stale_ttl=stale)
    if ent is None:
        return None
    body = ent.body
    return refresh(body) if refresh else body


def store_cached_json(
    namespace: str,
    key_suffix: tuple,
    body: dict[str, Any],
    *,
    app: Flask | None = None,
) -> None:
    key = cache_key(namespace, key_suffix, app=app)
    saved_at = _write_file(key, body)
    now = time.monotonic()
    with _lock:
        _mem_cache[key] = (saved_at, now, body)


def compute_lock(namespace: str, key_suffix: tuple) -> threading.Lock:
    key = cache_key(namespace, key_suffix)
    digest = _digest(key)
    with _lock:
        lk = _compute_locks.get(digest)
        if lk is None:
            lk = threading.Lock()
            _compute_locks[digest] = lk
        return lk


def _schedule_background_refresh(
    app: Flask,
    namespace: str,
    key_suffix: tuple,
    builder: Callable[[], dict[str, Any]],
) -> None:
    bound_app = real_flask_app(app)
    key = cache_key(namespace, key_suffix, app=bound_app)
    digest = _digest(key)
    with _lock:
        if digest in _refresh_inflight:
            return
        _refresh_inflight.add(digest)

    def _run() -> None:
        try:
            with league_test_request_context(bound_app):
                body = builder()
                store_cached_json(namespace, key_suffix, body, app=bound_app)
        except Exception:
            _log.exception("background cache refresh failed for %s %s", namespace, key_suffix)
        finally:
            with _lock:
                _refresh_inflight.discard(digest)

    threading.Thread(target=_run, daemon=True, name=f"cache-refresh-{digest[:8]}").start()


def get_or_build_cached_json_swr(
    namespace: str,
    key_suffix: tuple,
    *,
    fresh_ttl: float,
    stale_ttl: float,
    builder: Callable[[], dict[str, Any]],
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    app: Flask | None = None,
) -> tuple[dict[str, Any], str]:
    """Return payload and cache status (HIT-FRESH, HIT-STALE, MISS)."""
    app = real_flask_app(app or current_app)

    def _prepare(body: dict[str, Any]) -> dict[str, Any]:
        out = prefix_league_static_urls(body, app=app)
        return refresh(out) if refresh else out  # type: ignore[return-value]

    ent = get_cache_entry(
        namespace, key_suffix, fresh_ttl=fresh_ttl, stale_ttl=stale_ttl, app=app
    )
    if ent is not None:
        body = _prepare(ent.body)
        if not ent.is_fresh:
            _schedule_background_refresh(app, namespace, key_suffix, builder)
            return body, "HIT-STALE"
        return body, "HIT-FRESH"

    with compute_lock(namespace, key_suffix):
        ent = get_cache_entry(
            namespace, key_suffix, fresh_ttl=fresh_ttl, stale_ttl=stale_ttl, app=app
        )
        if ent is not None:
            body = _prepare(ent.body)
            status = "HIT-FRESH" if ent.is_fresh else "HIT-STALE"
            if not ent.is_fresh:
                _schedule_background_refresh(app, namespace, key_suffix, builder)
            return body, status
        core = builder()
        store_cached_json(namespace, key_suffix, core)
        return _prepare(core), "MISS"


def get_or_build_cached_json(
    namespace: str,
    key_suffix: tuple,
    ttl_seconds: float,
    builder: Callable[[], dict[str, Any]],
    *,
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    stale_ttl_seconds: float | None = None,
) -> dict[str, Any]:
    stale = float(
        stale_ttl_seconds
        if stale_ttl_seconds is not None
        else DEFAULT_STALE_TTL_SECONDS.get(namespace, max(ttl_seconds * 30, ttl_seconds + 120))
    )
    body, _status = get_or_build_cached_json_swr(
        namespace,
        key_suffix,
        fresh_ttl=ttl_seconds,
        stale_ttl=stale,
        builder=builder,
        refresh=refresh,
    )
    return body


def invalidate_league_json_cache(
    *,
    league_slug: str | None = None,
    namespace: str | None = None,
) -> None:
    """Drop cached JSON (optional league and/or namespace filter)."""
    with _lock:
        if league_slug is None and namespace is None:
            _mem_cache.clear()
        else:
            slug = str(league_slug).strip() if league_slug else None
            ns = str(namespace).strip() if namespace else None
            for k in list(_mem_cache.keys()):
                if slug is not None and k[1] != slug:
                    continue
                if ns is not None and k[0] != ns:
                    continue
                del _mem_cache[k]
    try:
        cache_dir = _cache_root()
        for p in cache_dir.glob("*.json"):
            name = p.name
            if league_slug is not None:
                prefix = str(league_slug).replace("/", "_") + "__"
                if not name.startswith(prefix):
                    continue
            if namespace is not None:
                needle = f"__{str(namespace).replace('/', '_')}__"
                if needle not in name:
                    continue
            p.unlink(missing_ok=True)
    except OSError:
        pass


def ttl_for_namespace(namespace: str, *, live: bool = False) -> float:
    if live:
        return DEFAULT_FRESH_TTL_SECONDS.get(f"{namespace}_live", 60.0)
    return DEFAULT_FRESH_TTL_SECONDS.get(
        f"{namespace}_final", DEFAULT_FRESH_TTL_SECONDS.get(namespace, 90.0)
    )


def stale_ttl_for_namespace(namespace: str, *, live: bool = False) -> float:
    if live:
        return DEFAULT_STALE_TTL_SECONDS.get(f"{namespace}_live", 600.0)
    return DEFAULT_STALE_TTL_SECONDS.get(
        f"{namespace}_final", DEFAULT_STALE_TTL_SECONDS.get(namespace, 7200.0)
    )


def fresh_ttl_from_config(app: Flask, namespace: str, *, default: float) -> float:
    key = f"LEAGUE_CACHE_FRESH_{namespace.upper()}"
    raw = app.config.get(key)
    if raw is not None:
        try:
            return max(5.0, float(raw))
        except (TypeError, ValueError):
            pass
    return default


def stale_ttl_from_config(app: Flask, namespace: str, *, default: float) -> float:
    key = f"LEAGUE_CACHE_STALE_{namespace.upper()}"
    raw = app.config.get(key)
    if raw is not None:
        try:
            return max(30.0, float(raw))
        except (TypeError, ValueError):
            pass
    return default
