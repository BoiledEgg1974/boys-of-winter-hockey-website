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


def _find_logo_file(league_dir: Path, stem: str) -> Path | None:
    """Resolve a logo file under ``league_dir`` (case-insensitive stem match)."""
    for ext in ("png", "webp", "jpg", "jpeg", "svg"):
        exact = league_dir / f"{stem}.{ext}"
        if exact.is_file():
            return exact
    want_prefix = f"{stem}.".lower()
    try:
        for p in league_dir.iterdir():
            if not p.is_file():
                continue
            if p.name.lower().startswith(want_prefix) and p.suffix.lower() in (
                ".png",
                ".webp",
                ".jpg",
                ".jpeg",
                ".svg",
            ):
                return p
    except OSError:
        return None
    return None


def _team_logo_stems(team) -> list[str]:
    """Filename stems to probe under this league's ``TEAM_LOGOS_REL_DIR``."""
    slug = team.slug
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
    return stems


def team_has_dedicated_league_logo(team) -> bool:
    """True when a non-placeholder logo file exists for this roster team in the league folder."""
    static_root = Path(current_app.static_folder or "")
    league_rel = current_app.config.get("TEAM_LOGOS_REL_DIR", "logos/teams")
    league_rel = str(league_rel).strip("/\\") or "logos/teams"
    league_dir = static_root / league_rel
    for stem in _team_logo_stems(team):
        if _find_logo_file(league_dir, stem) is not None:
            return True
    return False


def team_logo_url_for_team(team) -> str:
    """Return URL for a Team model's logo, or placeholder if missing."""
    static_root = Path(current_app.static_folder or "")
    league_rel = current_app.config.get("TEAM_LOGOS_REL_DIR", "logos/teams")
    league_rel = str(league_rel).strip("/\\") or "logos/teams"
    league_dir = static_root / league_rel
    legacy_dir = static_root / "logos" / "teams"

    for stem in _team_logo_stems(team):
        p = _find_logo_file(league_dir, stem)
        if p is not None:
            rel = p.relative_to(static_root).as_posix()
            return url_for("static", filename=rel)
        p_legacy = _find_logo_file(legacy_dir, stem)
        if p_legacy is not None:
            rel = p_legacy.relative_to(static_root).as_posix()
            return url_for("static", filename=rel)
    p_placeholder = league_dir / "placeholder.svg"
    if p_placeholder.is_file():
        return url_for("static", filename=f"{league_rel}/placeholder.svg")
    return url_for("static", filename="logos/teams/placeholder.svg")
