"""Choose which saved rank snapshot to use as the CHG / Δ baseline (site DB).

After a CSV import we append a snapshot that usually matches the live computed order, which
would make every CHG show 0. When a second snapshot exists, the newest row matches the current
order, and that row is **recent**, we fall back to the **prior** snapshot so the UI reflects
movement vs the last materially different saved order — without manual admin baselines.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Type

from sqlalchemy import select

from app.league_db import db


def ranks_dict_from_snapshot_json(raw: str | None) -> dict[int, int]:
    """Parse ``ranks_json`` from a snapshot row into entity id -> rank (1 = best)."""
    try:
        obj = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(obj, dict):
        return {}
    out: dict[int, int] = {}
    for k, v in obj.items():
        try:
            out[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return out


def select_rank_baseline_map(
    league_slug: str,
    current_rank_map: dict[int, int],
    snapshot_model: Type[Any],
    *,
    recent_hours: int = 24,
) -> dict[int, int]:
    """Return rank map (entity id -> rank) to pass into trend helpers.

    ``snapshot_model`` must have ``league_slug``, ``snapshot_at``, and ``ranks_json`` columns
    (``PowerRankSnapshot``, ``ProspectSystemRankSnapshot``, ``PositionalRankSnapshot``,
    ``ProspectLeagueRankSnapshot``).
    """
    slug = (league_slug or "").strip()
    if not slug or not current_rank_map:
        return {}
    rows = list(
        db.session.scalars(
            select(snapshot_model)
            .where(snapshot_model.league_slug == slug)
            .order_by(snapshot_model.snapshot_at.desc())
            .limit(2)
        ).all()
    )
    if not rows:
        return {}
    latest = ranks_dict_from_snapshot_json(getattr(rows[0], "ranks_json", None))
    if len(rows) >= 2:
        prior = ranks_dict_from_snapshot_json(getattr(rows[1], "ranks_json", None))
        snap_ts = getattr(rows[0], "snapshot_at", None)
        recent = False
        if snap_ts is not None:
            try:
                recent = (datetime.utcnow() - snap_ts) <= timedelta(hours=int(recent_hours))
            except TypeError:
                recent = False
        if current_rank_map == latest and latest != prior and recent:
            return prior
    return latest
