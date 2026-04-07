"""Read ``player_contract.csv`` for fields not stored on ``PlayerContract`` (e.g. years left by season)."""
from __future__ import annotations

from pathlib import Path

from app.config import Config
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized

_cache: dict[str, dict] | None = None
_cache_path: Path | None = None
_cache_mtime: float | None = None


def _contract_row_map() -> dict[str, dict]:
    global _cache, _cache_path, _cache_mtime
    path = Config.RAW_IMPORT_DIR / "player_contract.csv"
    if not path.is_file():
        return {}
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
    return _cache


def contract_years_remaining_major(fhm_player_id: str | None, season_start_year: int | None) -> int | None:
    """Count NHL (Major) salary seasons from the current league start year forward until ``-1``.

    Uses per-year ``major_YYYY`` columns from ``player_contract.csv``. Returns ``None`` if the
    file or player row is missing, or if no seasons remain.
    """
    if not fhm_player_id or season_start_year is None:
        return None
    m = _contract_row_map()
    row = m.get(str(fhm_player_id).strip())
    if not row:
        return None
    y = int(season_start_year)
    n = 0
    while y < 2100:
        key = f"major_{y}"
        if key not in row:
            break
        raw = row.get(key)
        if raw is None:
            break
        try:
            v = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            break
        if v < 0:
            break
        n += 1
        y += 1
    return n if n > 0 else None
