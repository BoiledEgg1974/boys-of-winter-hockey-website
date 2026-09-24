"""Copy FHM ``graphics/logo_teams`` images into the league team logos folder (by roster slug)."""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from app.services.fhm_league_logos import resolve_fhm_saved_game_root
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int
from scripts.import_pipeline.fhm_loader import _slug

log = logging.getLogger(__name__)

_LOGO_EXTS = (".png", ".PNG", ".webp", ".jpg", ".jpeg")


def _legacy_stem(city: str, nickname: str) -> str:
    c = re.sub(r"[^a-z0-9]+", "_", (city or "").lower()).strip("_")
    n = re.sub(r"[^a-z0-9]+", "_", (nickname or "").lower()).strip("_")
    return f"{c}_{n}" if n else c


def _fhm_style_stem(city: str, nickname: str) -> str:
    """Match FHM ``logo_teams`` naming (``St.`` → ``st__``, ``Jr.`` → ``jr__``, apostrophes → ``_``)."""
    parts = [p for p in ((city or "").strip(), (nickname or "").strip()) if p]
    if not parts:
        return ""
    s = " ".join(parts).lower()
    s = re.sub(r"\bst\.", "st__", s)
    s = re.sub(r"\bjr\.", "jr__", s)
    s = s.replace("'", "_")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _candidate_stems(city: str, nickname: str, abbr: str) -> list[str]:
    stems: list[str] = []

    def add(raw: str) -> None:
        s = (raw or "").strip("_")
        if s and s not in stems:
            stems.append(s)

    add(_fhm_style_stem(city, nickname))
    add(_legacy_stem(city, nickname))
    nick = (nickname or "").strip()
    city_s = (city or "").strip()
    if nick:
        add(re.sub(r"[^a-z0-9]+", "_", nick.lower()).strip("_"))
    if city_s:
        add(re.sub(r"[^a-z0-9]+", "_", city_s.lower()).strip("_"))
    if abbr:
        add(abbr.lower())
    return stems


def resolve_fhm_logo_teams_dir(raw_dir: Path) -> Path | None:
    """Locate ``logo_teams`` (explicit path in ``saved_game_csv_paths.json`` or saved-game root)."""
    raw_dir = raw_dir.resolve()
    try:
        cfg_path = Path(__file__).resolve().parents[2] / "scripts" / "saved_game_csv_paths.json"
        if cfg_path.is_file():
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            slug_hint = raw_dir.name.replace("_", "-")
            for key in (f"{slug_hint}-logo-teams", "bowl-fantasy-logo-teams"):
                explicit = data.get(key)
                if explicit:
                    p = Path(str(explicit))
                    if p.is_dir():
                        return p
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        log.debug("logo_teams path from config skipped: %s", exc)

    save_root = resolve_fhm_saved_game_root(raw_dir)
    if save_root is not None:
        p = save_root / "graphics" / "logo_teams"
        if p.is_dir():
            return p
    return None


def _find_team_logo_file(logo_dir: Path, city: str, nickname: str, abbr: str) -> Path | None:
    for stem in _candidate_stems(city, nickname, abbr):
        for ext in _LOGO_EXTS:
            candidate = logo_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
    # Case-insensitive exact stem
    lower_map = {p.stem.lower(): p for p in logo_dir.iterdir() if p.is_file()}
    for stem in _candidate_stems(city, nickname, abbr):
        hit = lower_map.get(stem.lower())
        if hit is not None:
            return hit
    return None


def sync_fhm_team_logos(
    raw_dir: Path,
    dest_dir: Path,
    *,
    exclude_league_ids: frozenset[int] = frozenset({0, 1, 6}),
    logo_teams_dir: Path | None = None,
) -> int:
    """Copy farm/overseas team logos to ``dest_dir/{slug}.png`` for site ``team_logo_url_for_team``."""
    logo_dir = logo_teams_dir if logo_teams_dir is not None else resolve_fhm_logo_teams_dir(raw_dir)
    team_path = raw_dir / "team_data.csv"
    if logo_dir is None or not logo_dir.is_dir() or not team_path.is_file():
        log.info("No FHM logo_teams folder or team_data.csv for %s", raw_dir)
        return 0
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for _, row in read_csv_normalized(team_path).iterrows():
        r = row.to_dict()
        lid = to_int(cell_val(r, "leagueid", "league_id"))
        if lid is None or int(lid) in exclude_league_ids:
            continue
        tid = to_int(cell_val(r, "teamid", "team_id"))
        if tid is None:
            continue
        city = cell_val(r, "name") or ""
        nick = cell_val(r, "nickname") or ""
        abbr = cell_val(r, "abbr") or str(tid)
        src = _find_team_logo_file(logo_dir, city, nick, abbr)
        if src is None:
            continue
        slug = _slug(abbr, int(tid))
        dest = dest_dir / f"{slug}.png"
        try:
            shutil.copy2(src, dest)
            n += 1
        except OSError as exc:
            log.warning("Could not copy team logo %s -> %s: %s", src.name, dest, exc)
    if n:
        log.info("Synced %s FHM farm team logo(s) from %s -> %s", n, logo_dir, dest_dir)
    return n
