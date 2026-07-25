"""Choose which saved rank snapshot to use as the CHG / Δ baseline (site DB).

After a CSV import we append a snapshot that usually matches the live computed order, which
would make every CHG show 0. When a second snapshot exists, the newest row matches the current
order, and that row is **recent**, we fall back to an older snapshot so the UI reflects
movement vs the last materially different saved order — without manual admin baselines.

The fallback baseline must cover the **same entity set** as the live ranking. Comparing
against a pre-expansion (or pre-contraction) snapshot would mark brand-new teams as NEW
forever in that window and distort everyone else's deltas.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Type

from sqlalchemy import select

from app.league_db import db

# How many recent snapshots to consider when looking past a roster-change snap.
_BASELINE_LOOKBACK = 12


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


def _entity_ids(ranks: dict[int, int]) -> frozenset[int]:
    return frozenset(ranks.keys())


def _snapshot_is_recent(snap_ts: Any, recent_hours: int) -> bool:
    if snap_ts is None:
        return False
    try:
        return (datetime.utcnow() - snap_ts) <= timedelta(hours=int(recent_hours))
    except TypeError:
        return False


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
            .limit(_BASELINE_LOOKBACK)
        ).all()
    )
    if not rows:
        return {}
    latest = ranks_dict_from_snapshot_json(getattr(rows[0], "ranks_json", None))
    if not latest:
        return {}

    # Right after import, live order matches the newest snapshot. Prefer an older
    # same-roster snapshot with a different order so CHG is meaningful.
    if (
        current_rank_map == latest
        and _snapshot_is_recent(getattr(rows[0], "snapshot_at", None), recent_hours)
    ):
        current_ids = _entity_ids(current_rank_map)
        for row in rows[1:]:
            prior = ranks_dict_from_snapshot_json(getattr(row, "ranks_json", None))
            if not prior or prior == latest:
                continue
            if _entity_ids(prior) != current_ids:
                # Expansion/contraction: skipping avoids perpetual NEW + skewed Δ.
                continue
            return prior
        # No compatible different prior — keep latest (all CHG 0) rather than NEW.
        return latest

    return latest
