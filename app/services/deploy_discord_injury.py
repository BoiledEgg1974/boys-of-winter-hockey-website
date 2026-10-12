"""Sidecar: queue Relegation #injury-list only after BOWL-Site-Update import + deploy-db.

Local FHM import marks ``bowl-fantasy`` pending; ``deploy-db`` uploads this JSON and
``notify_discord_after_db_deploy`` drains it on the VPS (then clears the marker).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

DEPLOY_DISCORD_INJURY_DIRNAME = ".deploy_discord_injury"
INJURY_DEPLOY_LEAGUE_SLUG = "bowl-fantasy"


def deploy_discord_injury_dir(instance_root: Path | None = None) -> Path:
    if instance_root is not None:
        root = Path(instance_root)
    else:
        from app.config import BASE_DIR

        root = Path(BASE_DIR) / "instance"
    return root / DEPLOY_DISCORD_INJURY_DIRNAME


def deploy_discord_injury_path(league_slug: str, *, instance_root: Path | None = None) -> Path:
    slug = str(league_slug or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in slug) or "league"
    return deploy_discord_injury_dir(instance_root) / f"{safe}.json"


def list_deploy_discord_injury_files(instance_root: Path | None = None) -> list[Path]:
    directory = deploy_discord_injury_dir(instance_root)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.json"))


def mark_deploy_injury_report_pending(
    league_slug: str,
    *,
    instance_root: Path | None = None,
    source: str = "fhm_import",
) -> None:
    """Record that an import finished; injury Discord may run on next deploy-db notify."""
    slug = str(league_slug or "").strip()
    if slug != INJURY_DEPLOY_LEAGUE_SLUG:
        return
    path = deploy_discord_injury_path(slug, instance_root=instance_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "league_slug": slug,
        "source": str(source or "fhm_import"),
        "updated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        + "Z",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _log.info("Marked %s injury report pending for deploy-db Discord notify.", slug)


def load_deploy_injury_report_pending(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> dict[str, Any] | None:
    slug = str(league_slug or "").strip()
    path = deploy_discord_injury_path(slug, instance_root=instance_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def injury_report_pending_for_deploy(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> bool:
    return load_deploy_injury_report_pending(league_slug, instance_root=instance_root) is not None


def clear_deploy_injury_report_pending(
    league_slug: str,
    *,
    instance_root: Path | None = None,
) -> bool:
    slug = str(league_slug or "").strip()
    path = deploy_discord_injury_path(slug, instance_root=instance_root)
    if not path.is_file():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False
