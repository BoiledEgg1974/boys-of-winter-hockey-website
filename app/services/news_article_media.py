"""Optional image uploads for Around the League news articles (site static)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

_MAX_BYTES = 2_500_000
_ALLOWED_EXT = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})


def ext_from_upload_filename(name: str) -> str | None:
    base = (secure_filename(name) or "").lower()
    if not base or "." not in base:
        return None
    ext = Path(base).suffix.lower()
    return ext if ext in _ALLOWED_EXT else None


def save_news_article_image(
    file_storage: FileStorage | None,
    *,
    league_slug: str,
    article_id: int,
) -> str | None:
    """
    Save upload under app/static/img/news/<league_slug>/<id>.<ext>.
    Returns static-relative path (e.g. img/news/bowl-fantasy/12.png) or None if no valid file.
    """
    if file_storage is None or not file_storage.filename:
        return None
    ext = ext_from_upload_filename(file_storage.filename)
    if ext is None:
        return None
    slug_safe = "".join(c for c in league_slug if c.isalnum() or c in "-_") or "league"
    static_root = Path(current_app.root_path) / "static"
    dest_dir = static_root / "img" / "news" / slug_safe
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = file_storage.read(_MAX_BYTES + 1)
    if len(data) > _MAX_BYTES:
        return None
    out_name = f"{int(article_id)}{ext}"
    out_path = dest_dir / out_name
    for old in dest_dir.glob(f"{int(article_id)}.*"):
        if old.suffix.lower() in _ALLOWED_EXT:
            try:
                old.unlink()
            except OSError:
                pass
    out_path.write_bytes(data)
    return f"img/news/{slug_safe}/{out_name}"
