"""File-backed JSON cache shared across uWSGI workers (Historical, Cap, Fantasy)."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from pathlib import Path
from threading import Lock
from typing import Any

from flask import Flask, current_app

from app.config import BASE_DIR
from app.services.layout_nav_cache import league_engine_sqlite_fingerprint

_lock = Lock()
_mem_cache: dict[tuple, tuple[float, dict[str, Any]]] = {}
_compute_locks: dict[str, Lock] = {}

DEFAULT_TTL_SECONDS: dict[str, float] = {
    "homepage_summary": 90.0,
    "playoff_bracket": 300.0,
    "game_boxscore_final": 3600.0,
    "game_boxscore_live": 45.0,
    "game_preview_final": 3600.0,
    "game_preview_live": 60.0,
    "player_hover": 120.0,
    "team_hover": 120.0,
    "search_players": 30.0,
}


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
    slug = str(app.config.get("LEAGUE_SLUG") or "")
    return f"league:mem:{slug}"


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


def _read_file(key: tuple, ttl_seconds: float) -> dict[str, Any] | None:
    path = _file_path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("key") != list(key):
            return None
        if (time.time() - float(data.get("saved_at", 0))) > ttl_seconds:
            return None
        body = data.get("body")
        return body if isinstance(body, dict) else None
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _write_file(key: tuple, body: dict[str, Any]) -> None:
    path = _file_path(key)
    payload = {"saved_at": time.time(), "key": list(key), "body": body}
    try:
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    except OSError:
        pass


def get_cached_json(
    namespace: str,
    key_suffix: tuple,
    ttl_seconds: float,
    *,
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    key = cache_key(namespace, key_suffix)
    now = time.monotonic()
    with _lock:
        hit = _mem_cache.get(key)
        if hit and (now - hit[0]) < ttl_seconds:
            body = hit[1]
            return refresh(body) if refresh else body

    file_body = _read_file(key, ttl_seconds)
    if file_body is not None:
        with _lock:
            _mem_cache[key] = (now, file_body)
        return refresh(file_body) if refresh else file_body
    return None


def store_cached_json(
    namespace: str,
    key_suffix: tuple,
    body: dict[str, Any],
) -> None:
    key = cache_key(namespace, key_suffix)
    now = time.monotonic()
    with _lock:
        _mem_cache[key] = (now, body)
    _write_file(key, body)


def compute_lock(namespace: str, key_suffix: tuple) -> Lock:
    key = cache_key(namespace, key_suffix)
    digest = _digest(key)
    with _lock:
        lk = _compute_locks.get(digest)
        if lk is None:
            lk = Lock()
            _compute_locks[digest] = lk
        return lk


def get_or_build_cached_json(
    namespace: str,
    key_suffix: tuple,
    ttl_seconds: float,
    builder: Callable[[], dict[str, Any]],
    *,
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return cached JSON or build once (with per-key lock to limit stampedes)."""
    cached = get_cached_json(namespace, key_suffix, ttl_seconds, refresh=refresh)
    if cached is not None:
        return cached
    with compute_lock(namespace, key_suffix):
        cached = get_cached_json(namespace, key_suffix, ttl_seconds, refresh=refresh)
        if cached is not None:
            return cached
        body = builder()
        store_cached_json(namespace, key_suffix, body)
        return refresh(body) if refresh else body


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
        return DEFAULT_TTL_SECONDS.get(f"{namespace}_live", 60.0)
    return DEFAULT_TTL_SECONDS.get(f"{namespace}_final", DEFAULT_TTL_SECONDS.get(namespace, 90.0))
