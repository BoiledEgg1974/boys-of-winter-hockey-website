"""Precomputed homepage dashboard snapshots stored in the league SQLite DB."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from typing import Any, Callable

from flask import Flask, current_app
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.league_urls import league_test_request_context, real_flask_app
from app.models import HomepageDashboardSnapshot, db
from app.services.homepage_summary_cache import _strip_volatile_fields
from app.services.league_json_cache import store_cached_json

_log = logging.getLogger(__name__)

_BOWL_SLUGS = frozenset({"bowl-historical", "bowl-cap", "bowl-fantasy"})
_REBUILD_INFLIGHT: set[str] = set()
_REBUILD_LOCK = threading.Lock()
_SEGMENTS = ("rs", "ps", "po")


def _snapshot_inflight_key(
    league_slug: str,
    segment: str,
    canonical_season_id: int,
    dashboard_season_id: int,
) -> str:
    return f"{league_slug}:{segment}:{canonical_season_id}:{dashboard_season_id}"


def _season_ids(
    canonical_season: object | None,
    dashboard_season: object | None,
) -> tuple[int, int]:
    return (
        int(getattr(canonical_season, "id", None) or 0),
        int(getattr(dashboard_season, "id", None) or 0),
    )


def load_ready_homepage_snapshot(
    session: Session,
    *,
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
) -> dict[str, Any] | None:
    """Return decoded payload when a ready snapshot exists for this key."""
    seg = segment if segment in _SEGMENTS else "rs"
    canonical_id, dashboard_id = _season_ids(canonical_season, dashboard_season)
    row = session.scalars(
        select(HomepageDashboardSnapshot).where(
            HomepageDashboardSnapshot.segment == seg,
            HomepageDashboardSnapshot.canonical_season_id == canonical_id,
            HomepageDashboardSnapshot.dashboard_season_id == dashboard_id,
            HomepageDashboardSnapshot.status == "ready",
        )
    ).first()
    if not row or not (row.payload_json or "").strip():
        return None
    try:
        body = json.loads(row.payload_json)
    except json.JSONDecodeError:
        return None
    if not isinstance(body, dict):
        return None
    body.pop("dashboard_summary_pending", None)
    return body


def save_homepage_snapshot(
    session: Session,
    *,
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
    body: dict[str, Any],
    status: str = "ready",
    error_message: str | None = None,
) -> None:
    """Upsert a snapshot row and mirror core JSON into the file cache."""
    seg = segment if segment in _SEGMENTS else "rs"
    canonical_id, dashboard_id = _season_ids(canonical_season, dashboard_season)
    league_slug = str(current_app.config.get("LEAGUE_SLUG") or "").strip()
    core = _strip_volatile_fields(body)
    core.pop("dashboard_summary_pending", None)
    payload = json.dumps(core, separators=(",", ":"))

    row = session.scalars(
        select(HomepageDashboardSnapshot).where(
            HomepageDashboardSnapshot.segment == seg,
            HomepageDashboardSnapshot.canonical_season_id == canonical_id,
            HomepageDashboardSnapshot.dashboard_season_id == dashboard_id,
        )
    ).first()
    if row is None:
        row = HomepageDashboardSnapshot(
            segment=seg,
            canonical_season_id=canonical_id,
            dashboard_season_id=dashboard_id,
            league_slug=league_slug,
        )
        session.add(row)
    row.league_slug = league_slug
    row.payload_json = payload
    row.built_at = datetime.utcnow()
    row.status = status
    row.error_message = (error_message or "").strip() or None
    session.commit()

    if status == "ready":
        store_cached_json(
            "homepage_summary",
            (seg, canonical_id, dashboard_id),
            core,
        )


def invalidate_homepage_dashboard_snapshots(session: Session) -> None:
    session.query(HomepageDashboardSnapshot).delete()
    session.commit()


def rebuild_homepage_snapshot(
    app: Flask,
    *,
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
    builder: Callable[[], dict[str, Any]],
) -> None:
    """Build one segment snapshot synchronously (used by warm/import workers)."""
    with league_test_request_context(app):
        slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
        seg = segment if segment in _SEGMENTS else "rs"
        canonical_id, dashboard_id = _season_ids(canonical_season, dashboard_season)
        try:
            save_homepage_snapshot(
                db.session,
                segment=seg,
                canonical_season=canonical_season,
                dashboard_season=dashboard_season,
                body={"status": "building"},
                status="building",
            )
            body = builder()
            save_homepage_snapshot(
                db.session,
                segment=seg,
                canonical_season=canonical_season,
                dashboard_season=dashboard_season,
                body=body,
                status="ready",
            )
            _log.info(
                "homepage snapshot ready for %s segment=%s seasons=%s/%s",
                slug,
                seg,
                canonical_id,
                dashboard_id,
            )
        except Exception as exc:
            db.session.rollback()
            _log.exception(
                "homepage snapshot build failed for %s segment=%s", slug, seg
            )
            try:
                save_homepage_snapshot(
                    db.session,
                    segment=seg,
                    canonical_season=canonical_season,
                    dashboard_season=dashboard_season,
                    body={},
                    status="failed",
                    error_message=str(exc),
                )
            except Exception:
                db.session.rollback()


def schedule_homepage_snapshot_rebuild(
    app: Flask,
    *,
    segment: str,
    canonical_season: object | None,
    dashboard_season: object | None,
    builder: Callable[[], dict[str, Any]],
) -> bool:
    """Queue a background snapshot rebuild if one is not already running."""
    bound_app = real_flask_app(app)
    slug = str(bound_app.config.get("LEAGUE_SLUG") or "").strip()
    if slug not in _BOWL_SLUGS:
        return False
    canonical_id, dashboard_id = _season_ids(canonical_season, dashboard_season)
    key = _snapshot_inflight_key(slug, segment, canonical_id, dashboard_id)
    with _REBUILD_LOCK:
        if key in _REBUILD_INFLIGHT:
            return False
        _REBUILD_INFLIGHT.add(key)

    def _run() -> None:
        try:
            rebuild_homepage_snapshot(
                bound_app,
                segment=segment,
                canonical_season=canonical_season,
                dashboard_season=dashboard_season,
                builder=builder,
            )
        finally:
            with _REBUILD_LOCK:
                _REBUILD_INFLIGHT.discard(key)

    threading.Thread(
        target=_run,
        daemon=True,
        name=f"homepage-snapshot-{slug.replace('/', '-')}-{segment}",
    ).start()
    return True


def warm_all_homepage_snapshots(app: Flask | None = None) -> None:
    """Build RS/PS/PO homepage snapshots in a background thread (all BOWL leagues)."""
    try:
        bound_app = real_flask_app(app if app is not None else current_app)
    except RuntimeError:
        return
    slug = str(bound_app.config.get("LEAGUE_SLUG") or "").strip()
    if slug not in _BOWL_SLUGS:
        return

    def _run() -> None:
        try:
            with league_test_request_context(bound_app):
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
                for seg in _SEGMENTS:
                    rebuild_homepage_snapshot(
                        bound_app,
                        segment=seg,
                        canonical_season=canonical,
                        dashboard_season=dashboard,
                        builder=lambda s=seg: _build_homepage_summary_payload(
                            s, canonical, dashboard
                        ),
                    )
        except Exception:
            _log.exception("homepage snapshot warm failed for %s", slug)

    threading.Thread(
        target=_run, daemon=True, name=f"homepage-snapshot-warm-{slug.replace('/', '-')}"
    ).start()

