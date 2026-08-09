"""Choose which saved rank snapshot to use as the CHG / Δ baseline (site DB).

After a CSV import we append a snapshot that usually matches the live computed order, which
would make every CHG show 0. When a second snapshot exists, the newest row matches the current
order, and that row is **recent**, we fall back to an older snapshot so the UI reflects
movement vs the last materially different saved order — without manual admin baselines.

For **team** rankings (power / positional / prospect-system), the baseline must cover the
**same entity set** as the live ranking. Comparing against a pre-expansion (or
pre-contraction) snapshot would mark brand-new teams as NEW forever and distort everyone
else's deltas. When no same-roster history exists yet, return the live map (all CHG 0).

Prospect **player** boards keep partial matches so newly ranked players can still show NEW.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Type

from sqlalchemy import select

from app.league_db import db

_log = logging.getLogger(__name__)

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


def entity_ids(ranks: dict[int, int]) -> frozenset[int]:
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
    require_same_entities: bool = True,
) -> dict[int, int]:
    """Return rank map (entity id -> rank) to pass into trend helpers.

    ``snapshot_model`` must have ``league_slug``, ``snapshot_at``, and ``ranks_json`` columns
    (``PowerRankSnapshot``, ``ProspectSystemRankSnapshot``, ``PositionalRankSnapshot``,
    ``ProspectLeagueRankSnapshot``).

    When ``require_same_entities`` is true (team boards), only same-roster snapshots are used.
    When false (prospect player boards), the latest snap may be a partial match so newcomers
    can show NEW.
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

    current_ids = entity_ids(current_rank_map)

    if require_same_entities:
        same: list[tuple[Any, dict[int, int]]] = []
        for row in rows:
            ranks = ranks_dict_from_snapshot_json(getattr(row, "ranks_json", None))
            if ranks and entity_ids(ranks) == current_ids:
                same.append((row, ranks))
        if not same:
            # Expansion/contraction with no compatible history yet — CHG 0, not perpetual NEW.
            return dict(current_rank_map)
        latest_row, latest = same[0]
        lookback = same[1:]
    else:
        latest = ranks_dict_from_snapshot_json(getattr(rows[0], "ranks_json", None))
        if not latest:
            return {}
        latest_row = rows[0]
        lookback = [(row, ranks_dict_from_snapshot_json(getattr(row, "ranks_json", None))) for row in rows[1:]]

    # Right after import, live order matches the newest compatible snapshot. Prefer an older
    # same-roster snapshot with a different order so CHG is meaningful.
    if current_rank_map == latest and _snapshot_is_recent(
        getattr(latest_row, "snapshot_at", None), recent_hours
    ):
        for _row, prior in lookback:
            if not prior or prior == latest:
                continue
            if entity_ids(prior) != current_ids:
                # Expansion/contraction: skipping avoids perpetual NEW + skewed Δ.
                continue
            return prior
        return latest

    return latest


def maybe_seed_rank_snapshot_for_roster_change(
    league_slug: str,
    current_rank_map: dict[int, int],
    snapshot_model: Type[Any],
    save: Callable[[], None],
) -> bool:
    """Persist a snapshot when the latest saved roster set differs from live (e.g. expansion).

    Returns True when a seed snapshot was written. Safe to call from page views — no-ops when
    the entity set already matches. Failures are logged and swallowed so standings still render.
    """
    slug = (league_slug or "").strip()
    if not slug or not current_rank_map:
        return False
    try:
        row = db.session.scalars(
            select(snapshot_model)
            .where(snapshot_model.league_slug == slug)
            .order_by(snapshot_model.snapshot_at.desc())
            .limit(1)
        ).first()
        latest = ranks_dict_from_snapshot_json(getattr(row, "ranks_json", None) if row else None)
        if entity_ids(latest) == entity_ids(current_rank_map):
            return False
        save()
        return True
    except Exception:
        _log.exception(
            "Failed seeding %s rank snapshot after roster change for %s",
            getattr(snapshot_model, "__tablename__", snapshot_model),
            slug,
        )
        return False
