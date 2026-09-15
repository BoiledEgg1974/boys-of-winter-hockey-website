"""Resolve static URLs for team and league logos (shared by context processor and API routes)."""
from __future__ import annotations

from pathlib import Path

from flask import current_app, has_app_context, url_for

# BOWL-Relegation (BLUP + BLOW): roster slug -> logo filename under ``logos/teams/bowl_fantasy/``.
# Keep in sync with ``data/imports/raw/bowl_fantasy/team_identity_history.csv``.
FANTASY_ROSTER_LOGO_FILES: dict[str, str] = {
    "bro-t9": "Seoul_Tigers.png",
    "cha-t4": "florida_party_animals.png",
    "col-t5": "Columbus_Chill.png",
    "den-t7": "Maine_Mist.png",
    "det-t12": "Sudbury_Blueberry_Bulldogs.png",
    "eme-t14": "Stockholm_Sentinels.png",
    "for-t1": "Prince_Albert_Stoners.png",
    "hou-t8": "New_Brunswick_Poseidon.png",
    "los-t2": "Seattle_Reign_Kings.png",
    "new-t10": "buffalo_sabres.png",
    "oma-t13": "Winnipeg_Trash_Pandas.png",
    "ott-t6": "Rideau__St_Lawrence_Kings.png",
    "pho-t0": "bangkok_roosters.png",
    "san-t11": "vancouver_canucks.png",
    "tor-t3": "toronto_six.png",
    "tuc-t15": "winnipeg_jets.png",
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
    if team is not None and has_app_context():
        league_slug = str(current_app.config.get("LEAGUE_SLUG") or "")
        if league_slug in ("bowl-historical", "bowl-cap", "bowl-fantasy") and not team_has_dedicated_league_logo(
            team
        ):
            try:
                from app.league_db import db
                from app.services.season_team_logo_bundle import get_season_team_logo_bundle
                from app.services.seasons import get_current_season

                season = get_current_season(db.session)
                sy = int(season.start_year) if season is not None and season.start_year is not None else None
                if sy is not None:
                    return get_season_team_logo_bundle().team_logo_url_for_season_context(team, sy)
            except Exception:
                pass

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


def league_logo_url() -> str:
    """Return URL for the current league logo, or the shared placeholder if missing."""
    static_root = Path(current_app.static_folder or "")
    slug = current_app.config.get("LEAGUE_SLUG")
    rel_dir = str(current_app.config.get("LEAGUE_LOGO_REL_DIR", "logos")).strip("/\\")
    specific_dir = static_root / rel_dir
    if specific_dir.is_dir():
        for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
            if (specific_dir / name).is_file():
                return url_for("static", filename=f"{rel_dir}/{name}")

    legacy_league_logo_dir = {
        "bowl-historical": "league2",
        "bowl-fantasy": "bow",
        "bowl-cap": "league3",
    }.get(str(slug or ""))
    if legacy_league_logo_dir:
        legacy_dir = static_root / "logos" / legacy_league_logo_dir
        if legacy_dir.is_dir():
            for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
                if (legacy_dir / name).is_file():
                    return url_for("static", filename=f"logos/{legacy_league_logo_dir}/{name}")

    if slug:
        slug_dir = static_root / "logos" / str(slug)
        if slug_dir.is_dir():
            for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
                if (slug_dir / name).is_file():
                    return url_for("static", filename=f"logos/{slug}/{name}")

    for name in ("league-logo.png", "league-logo.webp", "league-logo.svg"):
        if (static_root / "logos" / name).is_file():
            return url_for("static", filename=f"logos/{name}")
    return url_for("static", filename="logos/league-placeholder.svg")
