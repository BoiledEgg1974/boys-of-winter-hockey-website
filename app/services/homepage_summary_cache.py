"""Homepage /api/homepage/summary cache (uses :mod:`league_json_cache`)."""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from flask import Flask, current_app

from app.config import BASE_DIR
from app.services.homepage_dashboard import build_around_the_league
from app.services.homepage_modules import module_sort_order_map, module_visibility_map
from app.services.homepage_ticker import build_homepage_ticker_items
from app.services.league_json_cache import (
    DEFAULT_FRESH_TTL_SECONDS,
    DEFAULT_STALE_TTL_SECONDS,
    fresh_ttl_from_config,
    get_or_build_cached_json_swr,
    invalidate_league_json_cache,
    stale_ttl_from_config,
    store_cached_json,
)

_log = logging.getLogger(__name__)

_NAMESPACE = "homepage_summary"
_VOLATILE_KEYS = frozenset({"around_the_league", "module_settings", "ticker_items"})
_LEGACY_CACHE_DIR = BASE_DIR / "instance" / "homepage_summary_cache"
_BOWL_SLUGS = frozenset({"bowl-historical", "bowl-cap", "bowl-fantasy"})


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


def _homepage_fresh_ttl(app: Flask | None = None) -> float:
    app = app or current_app
    return fresh_ttl_from_config(
        app, _NAMESPACE, default=DEFAULT_FRESH_TTL_SECONDS[_NAMESPACE]
    )


def _homepage_stale_ttl(app: Flask | None = None) -> float:
    app = app or current_app
    return stale_ttl_from_config(
        app, _NAMESPACE, default=DEFAULT_STALE_TTL_SECONDS[_NAMESPACE]
    )


def refresh_volatile_homepage_fields(body: dict[str, Any]) -> dict[str, Any]:
    """Merge fresh news/module settings into a cached core payload (shallow copy)."""
    from app.models import db

    out = dict(body)
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


def build_homepage_summary_cached(
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
    builder,
) -> tuple[dict[str, Any], str]:
    """Return dashboard JSON and cache status (HIT-FRESH, HIT-STALE, MISS)."""
    app = current_app
    suffix = _summary_key_suffix(segment, canonical_season, dashboard_season)
    fresh_ttl = _homepage_fresh_ttl(app)
    stale_ttl = _homepage_stale_ttl(app)

    def _build_core() -> dict[str, Any]:
        return _strip_volatile_fields(builder())

    body, status = get_or_build_cached_json_swr(
        _NAMESPACE,
        suffix,
        fresh_ttl=fresh_ttl,
        stale_ttl=stale_ttl,
        builder=_build_core,
        app=app,
    )
    return refresh_volatile_homepage_fields(body), status


def warm_homepage_summary_cache(app: Flask | None = None) -> None:
    """Pre-build RS homepage cache in the background (per league worker)."""
    app = app or current_app
    slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
    if slug not in _BOWL_SLUGS:
        return
    if not app.config.get("LEAGUE_JSON_CACHE_WARM_ON_STARTUP", True):
        return

    def _run() -> None:
        try:
            with app.app_context():
                script_root = f"/{slug}".rstrip("/") or "/"
                with app.test_request_context(script_root + "/"):
                    from app.models import db
                    from app.routes.api import _build_homepage_summary_payload
                    from app.services.seasons import (
                        get_current_season,
                        season_with_imported_data_fallback,
                    )

                    canonical = get_current_season()
                    dashboard = (
                        season_with_imported_data_fallback(db.session, canonical)
                        if canonical
                        else None
                    )
                    build_homepage_summary_cached(
                        "rs",
                        canonical,
                        dashboard,
                        lambda: _build_homepage_summary_payload(
                            "rs", canonical, dashboard
                        ),
                    )
        except Exception:
            _log.exception("homepage cache warm failed for %s", slug)

    threading.Thread(
        target=_run, daemon=True, name=f"homepage-warm-{slug.replace('/', '-')}"
    ).start()


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
