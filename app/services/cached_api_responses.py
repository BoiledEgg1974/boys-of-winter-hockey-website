"""Helpers to return cached JSON API responses (all BOWL league mounts)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Response, current_app, jsonify

from app.services.league_json_cache import (
    DEFAULT_FRESH_TTL_SECONDS,
    DEFAULT_STALE_TTL_SECONDS,
    fresh_ttl_from_config,
    get_or_build_cached_json_swr,
    stale_ttl_from_config,
)


def jsonify_cached(
    namespace: str,
    key_suffix: tuple,
    ttl_seconds: float,
    builder: Callable[[], dict[str, Any]],
    *,
    cache_control: int = 30,
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    stale_ttl_seconds: float | None = None,
) -> Response:
    stale = float(
        stale_ttl_seconds
        if stale_ttl_seconds is not None
        else stale_ttl_from_config(
            current_app,
            namespace,
            default=DEFAULT_STALE_TTL_SECONDS.get(
                namespace, max(ttl_seconds * 30, ttl_seconds + 120)
            ),
        )
    )
    fresh = fresh_ttl_from_config(
        current_app, namespace, default=ttl_seconds
    )
    body, status = get_or_build_cached_json_swr(
        namespace,
        key_suffix,
        fresh_ttl=fresh,
        stale_ttl=stale,
        builder=builder,
        refresh=refresh,
    )
    resp = jsonify(body)
    resp.headers["Cache-Control"] = f"private, max-age={int(cache_control)}"
    resp.headers["X-Cache-Status"] = status
    return resp
