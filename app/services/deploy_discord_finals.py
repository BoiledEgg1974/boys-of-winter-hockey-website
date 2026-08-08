"""Persist newly-final game ids from local FHM import for deploy-db Discord notify.

Local imports enqueue against a blank Discord config, so boxscore events never
reach the live site DB. ``deploy-db`` uploads these JSON sidecars and the remote
``notify_discord_after_db_deploy`` script drains them against production routes.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

DEPLOY_DISCORD_FINALS_DIRNAME = ".deploy_discord_finals"


def deploy_discord_finals_dir(instance_root: Path | None = None) -> Path:
    if instance_root is not None:
        root = Path(instance_root)
    else:
        from app.config import BASE_DIR

        root = Path(BASE_DIR) / "instance"
    return root / DEPLOY_DISCORD_FINALS_DIRNAME


def deploy_discord_finals_path(league_slug: str, *, instance_root: Path | None = None) -> Path:
    slug = str(league_slug or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug) or "league"
    return deploy_discord_finals_dir(instance_root) / f"{safe}.json"


def record_deploy_newly_final_game_ids(
    league_slug: str,
    game_ids: set[int] | list[int] | None,
    *,
    instance_root: Path | None = None,
) -> int:
    """Merge newly final game ids into the deploy-db sidecar for ``league_slug``."""
    slug = str(league_slug or "").strip()
    if not slug or not game_ids:
        return 0
    incoming: set[int] = set()
    for gid in game_ids:
        try:
            incoming.add(int(gid))
        except (TypeError, ValueError):
            continue
    if not incoming:
        return 0
    path = deploy_discord_finals_path(slug, instance_root=instance_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_deploy_newly_final_game_ids(slug, instance_root=instance_root)
    before = len(existing)
    existing |= incoming
    payload: dict[str, Any] = {
        "league_slug": slug,
        "game_ids": sorted(existing),
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        + "Z",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    added = len(existing) - before
    _log.info(
        "Recorded %s newly-final game id(s) for deploy Discord notify (%s); file has %s.",
        added,
        slug,
        len(existing),
    )
    return added


def load_deploy_newly_final_game_ids(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> set[int]:
    path = deploy_discord_finals_path(league_slug, instance_root=instance_root)
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _log.exception("Could not read deploy Discord finals file %s", path)
        return set()
    if not isinstance(raw, dict):
        return set()
    out: set[int] = set()
    for gid in raw.get("game_ids") or []:
        try:
            out.add(int(gid))
        except (TypeError, ValueError):
            continue
    return out


def clear_deploy_newly_final_game_ids(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> bool:
    path = deploy_discord_finals_path(league_slug, instance_root=instance_root)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        _log.exception("Could not clear deploy Discord finals file %s", path)
        return False


def list_deploy_discord_finals_files(instance_root: Path | None = None) -> list[Path]:
    root = deploy_discord_finals_dir(instance_root)
    if not root.is_dir():
        return []
    return sorted(p for p in root.glob("*.json") if p.is_file())
