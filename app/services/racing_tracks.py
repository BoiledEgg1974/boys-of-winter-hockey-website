"""Resolve static track / arena art for racing result pages."""
from __future__ import annotations

import re
from pathlib import Path

from flask import current_app, has_app_context, url_for

from app.config import league_by_slug

_TRACK_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def _tracks_dir_key(league_slug: str) -> str:
    entry = league_by_slug(str(league_slug or "").strip())
    if entry is not None and entry.raw_import_dir:
        return str(entry.raw_import_dir)
    return str(league_slug or "").strip().replace("-", "_")


def _slug_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")


def resolve_track_image_static_filename(league_slug: str, track_name: str | None) -> str | None:
    """Return ``img/tracks/<league_dir>/<file>`` when art exists for ``track_name``."""
    name = str(track_name or "").strip()
    if not name or not has_app_context():
        return None
    static_root = current_app.static_folder
    if not static_root:
        return None
    folder_key = _tracks_dir_key(league_slug)
    base = Path(static_root) / "img" / "tracks" / folder_key
    if not base.is_dir():
        return None
    for ext in _TRACK_IMAGE_EXTS:
        candidate = base / f"{name}{ext}"
        if candidate.is_file():
            return f"img/tracks/{folder_key}/{candidate.name}"
    want = _slug_key(name)
    if not want:
        return None
    for path in sorted(base.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _TRACK_IMAGE_EXTS:
            continue
        if _slug_key(path.stem) == want:
            return f"img/tracks/{folder_key}/{path.name}"
    return None


def track_image_url(league_slug: str, track_name: str | None) -> str:
    rel = resolve_track_image_static_filename(league_slug, track_name)
    if not rel:
        return ""
    return url_for("static", filename=rel)
