"""Resolve static URLs for team logos (shared by context processor and API routes)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app, url_for

# BOWL-Fantasy: DB slugs are ``fw-t26``-style; many PNGs use descriptive stems
# (``fort_wayne_komets.png``). Try slug first, then this alias stem.
_FANTASY_LOGO_STEM_ALIASES: dict[str, str] = {
    "mon-t10": "moncton_wildcats",
    "kun-t11": "kunlun_red_star",
    "por-t12": "portland_buckaroos",
    "lon-t14": "london_black_knights",
    "pit-t15": "pittsburgh_penguins",
    "ken-t19": "kenya_pride",
    "mtl-t20": "montreal_canadiens",
    "bgk-t22": "bangkok_roosters",
    "can-t25": "canmore_eagles",
    "fw-t26": "fort_wayne_komets",
    "ham-t5": "hamilton_steel",
    "chi-t8": "chicago_blackhawks",
    "hal-t9": "halifax_privateers",
    "ind-t278": "indianapolis_racers",
    "me-t279": "maine_mariners",
}


def team_logo_url_for_team(team) -> str:
    """Return URL for a Team model's logo, or placeholder if missing."""
    slug = team.slug
    static_root = Path(current_app.static_folder or "")
    league_rel = current_app.config.get("TEAM_LOGOS_REL_DIR", "logos/teams")
    league_rel = str(league_rel).strip("/\\") or "logos/teams"
    league_dir = static_root / league_rel
    legacy_dir = static_root / "logos" / "teams"

    stems: list[str] = [slug]
    if str(current_app.config.get("LEAGUE_SLUG") or "") == "bowl-fantasy":
        alt = _FANTASY_LOGO_STEM_ALIASES.get(slug)
        if alt and alt not in stems:
            stems.append(alt)
    if str(current_app.config.get("LEAGUE_SLUG") or "") == "bowl-cap":
        # Static PNGs often use ``ATL-t227`` while DB slugs are ``atl-t227`` (case-sensitive URLs on Linux).
        fid = getattr(team, "fhm_team_id", None)
        ab = (getattr(team, "abbreviation", None) or "").strip()
        if fid is not None and ab:
            raw_id = str(fid).strip()
            for variant in (f"{ab.upper()}-t{raw_id}", f"{ab.lower()}-t{raw_id}", f"{ab}-t{raw_id}"):
                if variant and variant not in stems:
                    stems.append(variant)

    for stem in stems:
        for ext in ("png", "webp", "jpg", "svg"):
            p = league_dir / f"{stem}.{ext}"
            if p.is_file():
                return url_for("static", filename=f"{league_rel}/{stem}.{ext}")
            # Backward compatibility if logos are still in the old shared folder.
            p_legacy = legacy_dir / f"{stem}.{ext}"
            if p_legacy.is_file():
                return url_for("static", filename=f"logos/teams/{stem}.{ext}")
    p_placeholder = league_dir / "placeholder.svg"
    if p_placeholder.is_file():
        return url_for("static", filename=f"{league_rel}/placeholder.svg")
    return url_for("static", filename="logos/teams/placeholder.svg")
