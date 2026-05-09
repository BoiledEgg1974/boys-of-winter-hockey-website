"""Persisted baselines for homepage power rankings Change column (site DB)."""
from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from app.league_db import db
from app.services.homepage_dashboard import compute_power_rankings_payload
from app.services.prospect_system_rankings import apply_system_rank_trends
from app.services.seasons import get_current_season, season_with_imported_data_fallback
from app.site_models import PowerRankSnapshot


def load_latest_power_rank_snapshot(league_slug: str) -> tuple[dict[int, int], datetime | None]:
    slug = (league_slug or "").strip()
    if not slug:
        return {}, None
    row = db.session.scalars(
        select(PowerRankSnapshot)
        .where(PowerRankSnapshot.league_slug == slug)
        .order_by(PowerRankSnapshot.snapshot_at.desc())
        .limit(1)
    ).first()
    if not row:
        return {}, None
    try:
        raw = json.loads(row.ranks_json or "{}")
    except json.JSONDecodeError:
        return {}, row.snapshot_at
    out: dict[int, int] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out, row.snapshot_at


def save_power_rank_snapshot(league_slug: str, teams: list[dict[str, Any]]) -> None:
    """teams: ordered list from build_power_rankings (best first)."""
    ranks = {str(int(t["team_id"])): idx + 1 for idx, t in enumerate(teams)}
    snap = PowerRankSnapshot(
        league_slug=(league_slug or "").strip(),
        snapshot_at=datetime.utcnow(),
        ranks_json=json.dumps(ranks, sort_keys=True),
    )
    db.session.add(snap)
    db.session.commit()


def apply_power_rank_trends(teams: list[dict[str, Any]], prev_rank_by_team: dict[int, int]) -> None:
    """Mutate each team row with trend_dir / trend_delta vs previous power order (rank 1 = best)."""
    pseudo: list[dict[str, Any]] = []
    for i, t in enumerate(teams, start=1):
        tid = int(t["team_id"])
        pseudo.append({"rank": i, "team": SimpleNamespace(id=tid)})
    apply_system_rank_trends(pseudo, prev_rank_by_team)
    for t, p in zip(teams, pseudo, strict=True):
        t["trend_dir"] = p.get("trend_dir")
        t["trend_delta"] = p.get("trend_delta")


def record_power_rank_snapshot_after_import(app: object) -> None:
    """Append power-ranking order baseline after league CSV import (segment RS)."""
    with app.app_context():
        slug = str(app.config.get("LEAGUE_SLUG") or "").strip()
        if not slug:
            return
        canonical = get_current_season()
        if not canonical:
            return
        session = db.session
        season = season_with_imported_data_fallback(session, canonical)
        if not season:
            return
        logo_sy: int | None = int(season.start_year) if getattr(season, "start_year", None) is not None else None
        pr = compute_power_rankings_payload(
            session,
            season_id=int(season.id),
            segment="rs",
            logo_season_year=logo_sy,
        )
        teams = pr.get("teams") or []
        if not teams:
            return
        save_power_rank_snapshot(slug, teams)
