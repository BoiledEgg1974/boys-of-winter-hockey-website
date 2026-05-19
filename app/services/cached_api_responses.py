"""Helpers to return cached JSON API responses (all BOWL league mounts)."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from flask import Response, jsonify

from app.services.league_json_cache import get_or_build_cached_json


def jsonify_cached(
    namespace: str,
    key_suffix: tuple,
    ttl_seconds: float,
    builder: Callable[[], dict[str, Any]],
    *,
    cache_control: int = 30,
    refresh: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Response:
    body = get_or_build_cached_json(
        namespace,
        key_suffix,
        ttl_seconds,
        builder,
        refresh=refresh,
    )
    resp = jsonify(body)
    resp.headers["Cache-Control"] = f"private, max-age={int(cache_control)}"
    return resp
