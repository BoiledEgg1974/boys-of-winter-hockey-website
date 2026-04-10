"""Load a single row from the active RAW_IMPORT_DIR (e.g. data/imports/raw/<slug>/player_ratings.csv)."""
from __future__ import annotations

import math
from pathlib import Path

from flask import current_app, has_app_context

from app.config import Config
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized

_cache: dict[str, dict] | None = None
_cache_path: Path | None = None
_cache_mtime: float | None = None


def get_player_ratings_row(fhm_player_id: str | None) -> dict | None:
    """Return normalized column dict for PlayerId, or None if file missing / player not found."""
    global _cache, _cache_path, _cache_mtime
    if not fhm_player_id:
        return None
    raw_dir = (
        Path(current_app.config.get("RAW_IMPORT_DIR", Config.RAW_IMPORT_DIR))
        if has_app_context()
        else Path(Config.RAW_IMPORT_DIR)
    )
    path = raw_dir / "player_ratings.csv"
    if not path.is_file():
        return None
    mtime = path.stat().st_mtime
    if _cache is None or path != _cache_path or mtime != _cache_mtime:
        df = read_csv_normalized(path)
        _cache = {}
        for _, row in df.iterrows():
            r = row.to_dict()
            pid = cell_val(r, "playerid")
            if pid:
                _cache[str(pid).strip()] = r
        _cache_path = path
        _cache_mtime = mtime
    return _cache.get(str(fhm_player_id).strip())


ELIGIBLE_POSITION_DISPLAY_MIN_RATING: float = 14.0

_POSITION_RATING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("G", "g"),
    ("LD", "ld"),
    ("RD", "rd"),
    ("LW", "lw"),
    ("C", "c"),
    ("RW", "rw"),
)


def eligible_positions_from_ratings_row(
    rr: dict | None,
    min_rating: float = ELIGIBLE_POSITION_DISPLAY_MIN_RATING,
) -> str:
    """Return ``LW • C • RW``-style labels for positions rated at or above ``min_rating`` (FHM CSV)."""
    if not rr:
        return ""
    labels: list[str] = []
    for abbr, key in _POSITION_RATING_COLUMNS:
        raw = rr.get(key)
        if raw is None:
            continue
        if isinstance(raw, float) and math.isnan(raw):
            continue
        s = str(raw).strip()
        if not s or s.lower() == "nan":
            continue
        try:
            v = float(s)
        except ValueError:
            continue
        if v >= min_rating:
            labels.append(abbr)
    return " • ".join(labels)


def player_positions_display_label(player: object | None) -> str:
    """Positions with rating ≥ ``ELIGIBLE_POSITION_DISPLAY_MIN_RATING``; else DB ``position``."""
    if player is None:
        return "—"
    fid = getattr(player, "fhm_player_id", None)
    fid_s = str(fid).strip() if fid is not None and str(fid).strip() else None
    rr = get_player_ratings_row(fid_s)
    s = eligible_positions_from_ratings_row(rr, ELIGIBLE_POSITION_DISPLAY_MIN_RATING)
    if s:
        return s
    raw_pos = getattr(player, "position", None)
    return (str(raw_pos).strip() if raw_pos else "") or "—"
