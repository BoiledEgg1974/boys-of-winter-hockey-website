"""Copy FHM ``graphics/logo_leagues`` images into static storage keyed by ``fhm_league_id``."""
from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path

from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int

log = logging.getLogger(__name__)

_LOGO_EXTS = (".png", ".PNG", ".webp", ".jpg", ".jpeg")


def _slug_stem(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_")


def _saved_game_root_from_csv_path(csv_path: Path) -> Path | None:
    p = csv_path.resolve()
    if p.name.lower() == "csv" and p.parent.name.lower() == "import_export":
        root = p.parent.parent
        if (root / "graphics" / "logo_leagues").is_dir():
            return root
    return None


def resolve_fhm_saved_game_root(raw_dir: Path) -> Path | None:
    """``…/BOWL-Relegation.lg/import_export/csv`` → ``…/BOWL-Relegation.lg``."""
    raw_dir = raw_dir.resolve()
    for parent in (raw_dir, raw_dir.parent, raw_dir.parent.parent):
        if (parent / "graphics" / "logo_leagues").is_dir():
            return parent
    if raw_dir.name.lower() == "csv" and raw_dir.parent.name.lower() == "import_export":
        root = raw_dir.parent.parent
        if (root / "graphics" / "logo_leagues").is_dir():
            return root
    try:
        cfg_path = Path(__file__).resolve().parents[2] / "scripts" / "saved_game_csv_paths.json"
        if not cfg_path.is_file():
            return None
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        slug_hint = raw_dir.name.replace("_", "-")
        preferred = data.get(slug_hint) or data.get("bowl-fantasy")
        candidates: list[Path] = []
        if preferred:
            candidates.append(Path(str(preferred)))
        for csv_path in data.values():
            p = Path(str(csv_path))
            if p not in candidates:
                candidates.append(p)
        for csv_path in candidates:
            if csv_path.resolve() == raw_dir.resolve() or csv_path.parent.resolve() == raw_dir.resolve():
                root = _saved_game_root_from_csv_path(csv_path)
                if root is not None:
                    return root
        if preferred:
            root = _saved_game_root_from_csv_path(Path(str(preferred)))
            if root is not None:
                return root
    except (OSError, json.JSONDecodeError, TypeError) as exc:
        log.debug("Could not resolve saved game root from config: %s", exc)
    return None


def _find_league_logo_file(logo_dir: Path, *, name: str, abbr: str | None) -> Path | None:
    stems: list[str] = []
    for piece in (name, abbr or ""):
        s = _slug_stem(piece)
        if s and s not in stems:
            stems.append(s)
    for stem in stems:
        for ext in _LOGO_EXTS:
            candidate = logo_dir / f"{stem}{ext}"
            if candidate.is_file():
                return candidate
    abbr_l = (abbr or "").lower()
    if abbr_l:
        for ext in _LOGO_EXTS:
            candidate = logo_dir / f"{abbr_l}{ext}"
            if candidate.is_file():
                return candidate
    return None


def sync_fhm_league_logos(raw_dir: Path, dest_dir: Path) -> int:
    """Copy league logos from the saved game into ``dest_dir/{fhm_league_id}.png``."""
    save_root = resolve_fhm_saved_game_root(raw_dir)
    if save_root is None:
        log.info("No FHM saved game logo_leagues folder found for raw dir %s", raw_dir)
        return 0
    logo_dir = save_root / "graphics" / "logo_leagues"
    league_csv = raw_dir / "league_data.csv"
    if not league_csv.is_file():
        return 0
    dest_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    df = read_csv_normalized(league_csv)
    for _, row in df.iterrows():
        r = row.to_dict()
        lid = to_int(cell_val(r, "leagueid", "league_id"))
        if lid is None:
            continue
        name = cell_val(r, "name") or ""
        abbr = cell_val(r, "abbr")
        src = _find_league_logo_file(logo_dir, name=name, abbr=abbr)
        if src is None:
            continue
        dest = dest_dir / f"{int(lid)}.png"
        try:
            shutil.copy2(src, dest)
            n += 1
        except OSError as exc:
            log.warning("Could not copy league logo %s -> %s: %s", src.name, dest, exc)
    if n:
        log.info("Synced %s FHM league logo(s) from %s -> %s", n, logo_dir, dest_dir)
    return n
