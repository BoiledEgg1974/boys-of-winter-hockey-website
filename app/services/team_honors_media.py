"""Image uploads for team honors (retired jerseys and victory banners)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from app.services.news_article_media import ext_from_upload_filename

_MAX_BYTES = 2_500_000
_ALLOWED_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def _league_slug_safe(league_slug: str) -> str:
    return "".join(c for c in league_slug if c.isalnum() or c in "-_") or "league"


def _write_upload(
    file_storage: FileStorage,
    *,
    dest_dir: Path,
    out_name: str,
) -> bool:
    ext = ext_from_upload_filename(file_storage.filename)
    if ext is None:
        return False
    data = file_storage.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        return False
    dest_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(out_name).stem
    for old in dest_dir.glob(f"{stem}.*"):
        if old.suffix.lower() in _ALLOWED_EXT:
            try:
                old.unlink()
            except OSError:
                pass
    out_path = dest_dir / f"{stem}{ext}"
    out_path.write_bytes(data)
    return True


def retired_jersey_filename(team_id: int, jersey_number: int) -> str:
    return f"T{int(team_id)}-Jersey{int(jersey_number)}"


def victory_banner_filename(team_id: int, victory_number: int) -> str:
    return f"T{int(team_id)}-Banner{int(victory_number)}"


def save_retired_jersey_image(
    file_storage: FileStorage | None,
    *,
    league_slug: str,
    team_id: int,
    jersey_number: int,
) -> str | None:
    """
    Save under app/static/team_honors/retired_numbers/<league_slug>/T{id}-Jersey{n>.<ext>.
    Returns static-relative path or None if no valid file.
    """
    if file_storage is None or not file_storage.filename:
        return None
    ext = ext_from_upload_filename(file_storage.filename)
    if ext is None:
        return None
    slug_safe = _league_slug_safe(league_slug)
    static_root = Path(current_app.root_path) / "static"
    dest_dir = static_root / "team_honors" / "retired_numbers" / slug_safe
    base = retired_jersey_filename(team_id, jersey_number)
    if not _write_upload(file_storage, dest_dir=dest_dir, out_name=base):
        return None
    return f"team_honors/retired_numbers/{slug_safe}/{base}{ext}"


def save_victory_banner_image(
    file_storage: FileStorage | None,
    *,
    league_slug: str,
    team_id: int,
    victory_number: int,
) -> str | None:
    """
    Save under app/static/team_honors/banners/<league_slug>/T{id}-Banner{n>.<ext>.
    Returns static-relative path or None if no valid file.
    """
    if file_storage is None or not file_storage.filename:
        return None
    ext = ext_from_upload_filename(file_storage.filename)
    if ext is None:
        return None
    slug_safe = _league_slug_safe(league_slug)
    static_root = Path(current_app.root_path) / "static"
    dest_dir = static_root / "team_honors" / "banners" / slug_safe
    base = victory_banner_filename(team_id, victory_number)
    if not _write_upload(file_storage, dest_dir=dest_dir, out_name=base):
        return None
    return f"team_honors/banners/{slug_safe}/{base}{ext}"
