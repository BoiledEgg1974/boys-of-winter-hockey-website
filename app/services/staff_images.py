"""Static staff headshot URLs (per league image folder)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app, url_for


def staff_images_rel_dir(league_slug: str) -> str:
    """Fantasy uses its own folder; Cap and Historical share ``bowl_cap``."""
    if str(league_slug or "") == "bowl-fantasy":
        return "staff/bowl_fantasy"
    return "staff/bowl_cap"


def staff_image_url(league_slug: str, staff_fhm_id: str | int | None) -> str | None:
    sid = str(staff_fhm_id or "").strip()
    if not sid:
        return None
    static_root = Path(current_app.static_folder or "")
    rel_dir = staff_images_rel_dir(league_slug)
    for ext in ("png", "webp", "jpg", "jpeg"):
        p = static_root / rel_dir / f"{sid}.{ext}"
        if p.is_file():
            return url_for("static", filename=f"{rel_dir}/{sid}.{ext}")
    return staff_placeholder_url()


def staff_placeholder_url() -> str:
    return url_for("static", filename="staff/placeholder.svg")
