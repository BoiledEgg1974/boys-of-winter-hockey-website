"""Optional server-rendered bootstrap for league home (leaders + standings only).

Reads the same disk-backed ``homepage_summary`` cache as ``/api/homepage/summary``
without triggering a cold rebuild, so HTML first paint stays fast.
"""
from __future__ import annotations

from typing import Any

from flask import current_app

from app.league_urls import prefix_league_static_urls
from app.services.homepage_summary_cache import _summary_key_suffix
from app.services.league_json_cache import (
    DEFAULT_FRESH_TTL_SECONDS,
    DEFAULT_STALE_TTL_SECONDS,
    fresh_ttl_from_config,
    get_cache_entry,
    stale_ttl_from_config,
)

_NAMESPACE = "homepage_summary"


def homepage_ssr_bootstrap_from_cache() -> dict[str, Any] | None:
    """Leaders + standings from cached RS summary, or ``None`` if SSR disabled or cache empty."""
    if not current_app.config.get("HOMEPAGE_SSR_LEADERS_STANDINGS"):
        return None
    try:
        from app.models import db
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
        if not dashboard:
            return None

        suffix = _summary_key_suffix("rs", canonical, dashboard, "combined")
        fresh = fresh_ttl_from_config(
            current_app,
            _NAMESPACE,
            default=DEFAULT_FRESH_TTL_SECONDS[_NAMESPACE],
        )
        stale = stale_ttl_from_config(
            current_app,
            _NAMESPACE,
            default=DEFAULT_STALE_TTL_SECONDS[_NAMESPACE],
        )
        ent = get_cache_entry(
            _NAMESPACE,
            suffix,
            fresh_ttl=fresh,
            stale_ttl=stale,
            include_site_db_fingerprint=False,
        )
        if ent is None:
            return None

        body = prefix_league_static_urls(ent.body, app=current_app)
        leaders = body.get("leaders")
        standings = body.get("standings_by_division") or body.get("standings_by_conference")
        if not isinstance(leaders, dict) and not isinstance(standings, list):
            return None
        out: dict[str, Any] = {"segment": "rs", "relegation_scope": "combined"}
        if isinstance(leaders, dict):
            out["leaders"] = leaders
        if isinstance(standings, list):
            out["standings_by_division"] = standings
        return out if len(out) > 2 else None
    except Exception:
        return None
