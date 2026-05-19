"""Resolve static URLs for team logos (shared by context processor and API routes)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app, url_for

# BOWL-Fantasy: roster slug → logo filename under ``logos/teams/bowl_fantasy/``.
# Keep in sync with ``data/imports/raw/bowl_fantasy/team_identity_history.csv``.
FANTASY_ROSTER_LOGO_FILES: dict[str, str] = {
    "bgk-t22": "bangkok_roosters.png",
    "can-t25": "canmore_eagles.png",
    "chi-t8": "chicago_blackhawks.png",
    "edm-t23": "edm-t23.png",
    "fla-t21": "fla-t21.png",
    "fw-t26": "fort_wayne_komets.png",
    "hal-t9": "halifax_privateers.png",
    "ham-t5": "hamilton_steel.png",
    "ind-t278": "indianapolis_racers.png",
    "ken-t19": "kenya_pride.png",
    "kun-t11": "kunlun_red_star.png",
    "lon-t14": "london_black_knights.png",
    "me-t279": "maine_mariners.png",
    "mon-t10": "moncton_wildcats.png",
    "mtl-t20": "montreal_canadiens.png",
    "pit-t15": "pittsburgh_penguins.png",
    "por-t12": "portland_buckaroos.png",
    "six-t18": "six-t18.png",
    "tok-t17": "tok-t17.png",
    "tor-t3": "tor-t3.png",
    "trl-t24": "trl-t24.png",
    "vcr-t280": "vcr-t280.png",
    "vic-t16": "vic-t16.png",
    "wic-t0": "wic-t0.png",
}

# When the PNG stem differs from the DB slug, probe the canonical filename second.
_FANTASY_LOGO_STEM_ALIASES: dict[str, str] = {
    slug: Path(filename).stem
    for slug, filename in FANTASY_ROSTER_LOGO_FILES.items()
    if Path(filename).stem != slug
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
