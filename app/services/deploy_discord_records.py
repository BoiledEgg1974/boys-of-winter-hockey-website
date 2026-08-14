"""Persist broken-record Discord events from local FHM import for deploy-db notify.

Local imports enqueue against a blank Discord config, so ``record_broken`` events
never reach the live site DB. ``deploy-db`` uploads these JSON sidecars and the
remote ``notify_discord_after_db_deploy`` script drains them against production
routes. A live-state stash (captured on the server before DB promote) recovers
breaks when the import sidecar is missing.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

DEPLOY_DISCORD_RECORDS_DIRNAME = ".deploy_discord_records"
DEPLOY_DISCORD_RECORDS_LIVE_DIRNAME = ".deploy_discord_records_live"


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    )


def _safe_slug(league_slug: str) -> str:
    slug = str(league_slug or "").strip()
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug) or "league"


def _instance_root(instance_root: Path | None = None) -> Path:
    if instance_root is not None:
        return Path(instance_root)
    from app.config import BASE_DIR

    return Path(BASE_DIR) / "instance"


def deploy_discord_records_dir(instance_root: Path | None = None) -> Path:
    return _instance_root(instance_root) / DEPLOY_DISCORD_RECORDS_DIRNAME


def deploy_discord_records_path(league_slug: str, *, instance_root: Path | None = None) -> Path:
    return deploy_discord_records_dir(instance_root) / f"{_safe_slug(league_slug)}.json"


def deploy_discord_records_live_dir(instance_root: Path | None = None) -> Path:
    return _instance_root(instance_root) / DEPLOY_DISCORD_RECORDS_LIVE_DIRNAME


def deploy_discord_records_live_path(
    league_slug: str, *, instance_root: Path | None = None
) -> Path:
    return deploy_discord_records_live_dir(instance_root) / f"{_safe_slug(league_slug)}.json"


def _normalize_event(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    source_id = str(raw.get("source_id") or "").strip()
    payload = raw.get("payload")
    if not source_id or not isinstance(payload, dict):
        return None
    return {"source_id": source_id, "payload": payload}


def record_deploy_record_break_events(
    league_slug: str,
    events: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    instance_root: Path | None = None,
) -> int:
    """Merge broken-record events into the deploy-db sidecar for ``league_slug``."""
    slug = str(league_slug or "").strip()
    if not slug or not events:
        return 0
    incoming: dict[str, dict[str, Any]] = {}
    for raw in events:
        event = _normalize_event(raw)
        if event is None:
            continue
        incoming[event["source_id"]] = event
    if not incoming:
        return 0
    existing = {
        event["source_id"]: event
        for event in load_deploy_record_break_events(slug, instance_root=instance_root)
    }
    before = len(existing)
    existing.update(incoming)
    path = deploy_discord_records_path(slug, instance_root=instance_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "league_slug": slug,
        "events": [existing[key] for key in sorted(existing)],
        "updated_at": _utc_now_iso(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    added = len(existing) - before
    _log.info(
        "Recorded %s broken-record event(s) for deploy Discord notify (%s); file has %s.",
        added,
        slug,
        len(existing),
    )
    return added


def load_deploy_record_break_events(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> list[dict[str, Any]]:
    path = deploy_discord_records_path(league_slug, instance_root=instance_root)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.exception("Could not read deploy Discord records file %s", path)
        return []
    if not isinstance(raw, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw.get("events") or []:
        event = _normalize_event(item)
        if event is None or event["source_id"] in seen:
            continue
        seen.add(event["source_id"])
        out.append(event)
    return out


def clear_deploy_record_break_events(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> bool:
    path = deploy_discord_records_path(league_slug, instance_root=instance_root)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        _log.exception("Could not clear deploy Discord records file %s", path)
        return False


def list_deploy_discord_records_files(instance_root: Path | None = None) -> list[Path]:
    root = deploy_discord_records_dir(instance_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.json") if p.is_file())


def save_live_record_state(
    league_slug: str,
    state: dict[str, Any],
    *,
    instance_root: Path | None = None,
) -> Path:
    """Write pre-promote live record snapshots/baselines for deploy-db fallback."""
    slug = str(league_slug or "").strip()
    path = deploy_discord_records_live_path(slug, instance_root=instance_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(state)
    payload["league_slug"] = slug
    payload["updated_at"] = _utc_now_iso()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def load_live_record_state(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> dict[str, Any] | None:
    path = deploy_discord_records_live_path(league_slug, instance_root=instance_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.exception("Could not read live record-state file %s", path)
        return None
    return raw if isinstance(raw, dict) else None


def clear_live_record_state(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> bool:
    path = deploy_discord_records_live_path(league_slug, instance_root=instance_root)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        _log.exception("Could not clear live record-state file %s", path)
        return False
