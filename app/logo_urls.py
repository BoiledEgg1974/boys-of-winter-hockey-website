"""Resolve static URLs for team logos (shared by context processor and API routes)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app, url_for


def team_logo_url_for_team(team) -> str:
    """Return URL for a Team model's logo, or placeholder if missing."""
    slug = team.slug
    static_root = Path(current_app.static_folder or "")
    league_rel = current_app.config.get("TEAM_LOGOS_REL_DIR", "logos/teams")
    league_rel = str(league_rel).strip("/\\") or "logos/teams"
    league_dir = static_root / league_rel
    legacy_dir = static_root / "logos" / "teams"
    for ext in ("png", "webp", "jpg", "svg"):
        p = league_dir / f"{slug}.{ext}"
        if p.is_file():
            return url_for("static", filename=f"{league_rel}/{slug}.{ext}")
        # Backward compatibility if logos are still in the old shared folder.
        p_legacy = legacy_dir / f"{slug}.{ext}"
        if p_legacy.is_file():
            return url_for("static", filename=f"logos/teams/{slug}.{ext}")
    p_placeholder = league_dir / "placeholder.svg"
    if p_placeholder.is_file():
        return url_for("static", filename=f"{league_rel}/placeholder.svg")
    return url_for("static", filename="logos/teams/placeholder.svg")
