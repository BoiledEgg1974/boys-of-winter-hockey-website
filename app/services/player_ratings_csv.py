"""Load a single row from the active RAW_IMPORT_DIR (e.g. data/imports/raw/<slug>/player_ratings.csv)."""
from __future__ import annotations

import math
import re
from pathlib import Path

from flask import current_app, has_app_context

from app.config import Config
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized

# One entry per resolved CSV path so multi-league apps in one process keep correct, warm caches.
_cache_entries: dict[str, tuple[float, dict[str, dict]]] = {}


def fhm_abi_pot_float(val: object) -> float | None:
    """Parse ability/potential cells that may include FHM grade suffixes (e.g. ``3Aa``)."""
    if val is None:
        return None
    if isinstance(val, float) and val != val:
        return None
    s = str(val).strip()
    if not s or s.lower() == "nan":
        return None
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def get_player_ratings_row(fhm_player_id: str | None) -> dict | None:
    """Return normalized column dict for PlayerId, or None if file missing / player not found."""
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
    path_key = str(path.resolve())
    mtime = path.stat().st_mtime
    ent = _cache_entries.get(path_key)
    if ent is None or ent[0] != mtime:
        df = read_csv_normalized(path)
        by_id: dict[str, dict] = {}
        for _, row in df.iterrows():
            r = row.to_dict()
            pid = cell_val(r, "playerid")
            if pid:
                by_id[str(pid).strip()] = r
        _cache_entries[path_key] = (mtime, by_id)
        ent = _cache_entries[path_key]
    return ent[1].get(str(fhm_player_id).strip())


ELIGIBLE_POSITION_DISPLAY_MIN_RATING: float = 14.0

_POSITION_RATING_COLUMNS: tuple[tuple[str, str], ...] = (
    ("G", "g"),
    ("LD", "ld"),
    ("RD", "rd"),
    ("LW", "lw"),
    ("C", "c"),
    ("RW", "rw"),
)

_POSITION_LONG_LABELS: tuple[str, ...] = (
    "Goalie",
    "Left Defense",
    "Right Defense",
    "Left Wing",
    "Center",
    "Right Wing",
)


def _parse_position_rating_float(raw: object) -> float | None:
    if raw is None:
        return None
    if isinstance(raw, float) and math.isnan(raw):
        return None
    s = str(raw).strip()
    if not s or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def position_ratings_display_list(rr: dict | None) -> list[dict[str, object]]:
    """Rows for the player profile / share card: long label, CSV abbr, parsed value, primary = max rating."""
    if not rr:
        return []
    parsed: list[tuple[str, str, str, float | None]] = []
    for (abbr, key), long_l in zip(_POSITION_RATING_COLUMNS, _POSITION_LONG_LABELS):
        v = _parse_position_rating_float(rr.get(key))
        parsed.append((long_l, abbr, key, v))
    best_idx: int | None = None
    best_val: float | None = None
    for i, (_, _, _, v) in enumerate(parsed):
        if v is None:
            continue
        if best_idx is None or best_val is None or v > best_val:
            best_idx = i
            best_val = v
    out: list[dict[str, object]] = []
    for i, (long_l, abbr, key, v) in enumerate(parsed):
        is_primary = best_idx is not None and i == best_idx
        out.append(
            {
                "label": long_l,
                "abbr": abbr,
                "key": key,
                "value": v,
                "is_primary": is_primary,
            }
        )
    return out


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
