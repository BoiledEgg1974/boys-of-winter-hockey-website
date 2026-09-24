"""Enqueue Discord injury deltas after FHM import (BOWL-Relegation)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.site_models import InjuryImportSnapshot


def _snapshot_rows(session: Session) -> list[dict[str, Any]]:
    from app.models import Team
    from app.services.injuries import injury_payload_league_wide
    from app.services.relegation import filter_teams_to_main_tiers, get_tier_config

    tier_cfg = get_tier_config(session)
    main_ids = frozenset(
        int(t.id)
        for t in filter_teams_to_main_tiers(list(session.scalars(select(Team)).all()), tier_cfg)
    )
    rows = injury_payload_league_wide(session, main_ids)
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "player_id": int(row["player_id"]),
                "team_id": int(row["team_id"]) if row.get("team_id") else None,
                "injury_name": str(row.get("injury_name") or ""),
                "recovery_days": row.get("recovery_days"),
                "status": row.get("status"),
            }
        )
    out.sort(key=lambda r: (int(r["player_id"]), str(r.get("injury_name") or "")))
    return out


def _row_key(row: dict[str, Any]) -> str:
    return f"{row['player_id']}:{row.get('injury_name')}:{row.get('recovery_days')}:{row.get('team_id')}"


def diff_injury_snapshots(
    previous: list[dict[str, Any]], current: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    prev_by_player = {int(r["player_id"]): r for r in previous}
    cur_by_player = {int(r["player_id"]): r for r in current}
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    for pid, row in cur_by_player.items():
        if pid not in prev_by_player:
            added.append(row)
        elif _row_key(prev_by_player[pid]) != _row_key(row):
            changed.append({"before": prev_by_player[pid], "after": row})
    for pid, row in prev_by_player.items():
        if pid not in cur_by_player:
            removed.append(row)
    return {"added": added, "removed": removed, "changed": changed}


def maybe_enqueue_injury_report_delta(session: Session, league_slug: str) -> bool:
    """Compare injury CSV state to last snapshot; enqueue Discord event when changed."""
    slug = str(league_slug or "").strip()
    if slug != "bowl-fantasy":
        return False
    from app.services.injuries import injuries_supported_for_league

    if not injuries_supported_for_league(slug):
        return False

    current = _snapshot_rows(session)
    snap = session.scalar(
        select(InjuryImportSnapshot).where(InjuryImportSnapshot.league_slug == slug).limit(1)
    )
    previous: list[dict[str, Any]] = []
    if snap is not None:
        try:
            raw = json.loads(snap.snapshot_json or "[]")
            previous = raw if isinstance(raw, list) else []
        except (TypeError, ValueError, json.JSONDecodeError):
            previous = []

    delta = diff_injury_snapshots(previous, current)
    if not delta["added"] and not delta["removed"] and not delta["changed"]:
        if snap is None:
            session.add(
                InjuryImportSnapshot(
                    league_slug=slug,
                    snapshot_json=json.dumps(current),
                    updated_at=datetime.utcnow(),
                )
            )
        return False

    digest = hashlib.sha256(json.dumps(current, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    batch_id = datetime.utcnow().strftime("%Y%m%d%H%M") + "-" + digest

    from app.league_db import db
    from app.services.discord_events import enqueue_discord_event

    enqueue_discord_event(
        db.session,
        league_slug=slug,
        event_key="injury_report_delta",
        payload={
            "title": "Injury report update",
            "added": delta["added"],
            "removed": delta["removed"],
            "changed": delta["changed"],
            "total_active": len(current),
        },
        created_by_user_id=None,
        source_type="fhm_import",
        source_id=batch_id,
    )

    if snap is None:
        snap = InjuryImportSnapshot(league_slug=slug, snapshot_json="[]")
        session.add(snap)
    snap.snapshot_json = json.dumps(current)
    snap.updated_at = datetime.utcnow()
    return True
