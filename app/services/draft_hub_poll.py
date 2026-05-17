"""Draft Hub live poll helpers: throttle timer ticks across many concurrent clients."""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.league_db import commit_or_release_after_tick
from app.models import Player
from app.services.draft_hub_state import process_tick, utcnow_naive

_TICK_INTERVAL_SECONDS = 1.0
_last_tick_at: dict[int, float] = {}


def maybe_process_tick(session: Session, draft: Any) -> None:
    """Run ``process_tick`` at most once per second per draft unless the deadline has passed."""
    if draft is None or getattr(draft, "status", None) != "live":
        return
    draft_id = int(draft.id)
    ddl = getattr(draft, "pick_deadline_at", None)
    if ddl is not None and utcnow_naive() > ddl:
        process_tick(session, draft)
        commit_or_release_after_tick(session, draft)
        _last_tick_at[draft_id] = time.monotonic()
        return
    now = time.monotonic()
    last = _last_tick_at.get(draft_id, 0.0)
    if now - last < _TICK_INTERVAL_SECONDS:
        return
    _last_tick_at[draft_id] = now
    process_tick(session, draft)
    commit_or_release_after_tick(session, draft)


def players_by_id(session: Session, player_ids: set[int]) -> dict[int, Player]:
    if not player_ids:
        return {}
    return {
        int(p.id): p
        for p in session.scalars(select(Player).where(Player.id.in_(player_ids))).unique().all()
    }
