"""Homepage /api/homepage/summary cache (uses :mod:`league_json_cache`)."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from flask import current_app

from app.config import BASE_DIR
from app.services.homepage_dashboard import build_around_the_league
from app.services.homepage_modules import module_sort_order_map, module_visibility_map
from app.services.homepage_ticker import build_homepage_ticker_items
from app.services.league_json_cache import (
    DEFAULT_TTL_SECONDS,
    compute_lock,
    get_cached_json,
    get_or_build_cached_json,
    invalidate_league_json_cache,
    store_cached_json,
)

_NAMESPACE = "homepage_summary"
_VOLATILE_KEYS = frozenset({"around_the_league", "module_settings", "ticker_items"})
_LEGACY_CACHE_DIR = BASE_DIR / "instance" / "homepage_summary_cache"


def _summary_key_suffix(
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
) -> tuple:
    return (
        str(segment).strip(),
        int(getattr(canonical_season, "id", None) or 0),
        int(getattr(dashboard_season, "id", None) or 0),
    )


def _strip_volatile_fields(body: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in body.items() if k not in _VOLATILE_KEYS}


def _news_dashboard_viewer() -> Any | None:
    from flask_login import current_user

    return current_user if getattr(current_user, "is_authenticated", False) else None


def refresh_volatile_homepage_fields(body: dict[str, Any]) -> dict[str, Any]:
    from app.models import db

    out = copy.deepcopy(body)
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
    logo_sy = None
    try:
        from app.services.seasons import get_current_season

        season = get_current_season()
        if season is not None and getattr(season, "start_year", None) is not None:
            logo_sy = int(season.start_year)
    except Exception:
        logo_sy = None
    out["around_the_league"] = build_around_the_league(
        db.session, _news_dashboard_viewer(), logo_season_year=logo_sy
    )
    out["module_settings"] = {
        "visibility": module_visibility_map(db.session, league_slug),
        "sort_order": module_sort_order_map(db.session, league_slug),
    }
    out["ticker_items"] = build_homepage_ticker_items(out)
    return out


def get_cached_homepage_summary(
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
) -> dict[str, Any] | None:
    ttl = DEFAULT_TTL_SECONDS[_NAMESPACE]
    suffix = _summary_key_suffix(segment, canonical_season, dashboard_season)
    core = get_cached_json(_NAMESPACE, suffix, ttl)
    if core is None:
        return None
    return refresh_volatile_homepage_fields(core)


def store_homepage_summary(
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
    body: dict[str, Any],
) -> None:
    suffix = _summary_key_suffix(segment, canonical_season, dashboard_season)
    store_cached_json(_NAMESPACE, suffix, _strip_volatile_fields(body))


def compute_lock_for_homepage_summary(
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
):
    return compute_lock(_NAMESPACE, _summary_key_suffix(segment, canonical_season, dashboard_season))


def build_homepage_summary_cached(
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
    builder,
) -> dict[str, Any]:
    suffix = _summary_key_suffix(segment, canonical_season, dashboard_season)
    ttl = DEFAULT_TTL_SECONDS[_NAMESPACE]

    def _build_core() -> dict[str, Any]:
        return _strip_volatile_fields(builder())

    core = get_or_build_cached_json(
        _NAMESPACE,
        suffix,
        ttl,
        _build_core,
    )
    return refresh_volatile_homepage_fields(core)


def invalidate_homepage_summary_cache(*, league_slug: str | None = None) -> None:
    invalidate_league_json_cache(league_slug=league_slug, namespace=_NAMESPACE)
    if league_slug is None:
        try:
            if _LEGACY_CACHE_DIR.is_dir():
                for p in _LEGACY_CACHE_DIR.glob("*.json"):
                    p.unlink(missing_ok=True)
        except OSError:
            pass
    else:
        prefix = str(league_slug).replace("/", "_") + "_"
        try:
            if _LEGACY_CACHE_DIR.is_dir():
                for p in _LEGACY_CACHE_DIR.glob(f"{prefix}*.json"):
                    p.unlink(missing_ok=True)
        except OSError:
            pass
