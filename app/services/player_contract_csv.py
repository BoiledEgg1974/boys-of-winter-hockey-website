"""Read ``player_contract.csv`` for fields not stored on ``PlayerContract`` (e.g. years left by season)."""
from __future__ import annotations

from pathlib import Path

from app.config import Config
from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized

# Per-file cache so multi-league apps (different RAW_IMPORT_DIR) each see correct rows.
_row_maps: dict[str, tuple[float, dict[str, dict]]] = {}


def _contract_row_map(path: Path) -> dict[str, dict]:
    if not path.is_file():
        return {}
    key = str(path.resolve())
    mtime = path.stat().st_mtime
    hit = _row_maps.get(key)
    if hit is not None and hit[0] == mtime:
        return hit[1]
    df = read_csv_normalized(path)
    m: dict[str, dict] = {}
    for _, row in df.iterrows():
        r = row.to_dict()
        pid = cell_val(r, "playerid")
        if pid:
            m[str(pid).strip()] = r
    _row_maps[key] = (mtime, m)
    return m


def contract_years_remaining_major(
    fhm_player_id: str | None,
    season_start_year: int | None,
    raw_import_dir: Path | None = None,
) -> int | None:
    """Count NHL (Major) salary seasons from the current league start year forward until ``-1``.

    Uses per-year ``major_YYYY`` columns from ``player_contract.csv``. Returns ``None`` if the
    file or player row is missing, or if no seasons remain.
    """
    if not fhm_player_id or season_start_year is None:
        return None
    base = raw_import_dir if raw_import_dir is not None else Path(Config.RAW_IMPORT_DIR)
    path = base / "player_contract.csv"
    m = _contract_row_map(path)
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


def contract_final_season_label_from_remaining(
    years_remaining_major: int | None,
    season_start_year: int | None,
) -> str | None:
    """Last NHL season label (e.g. ``2038–39``) from a :func:`contract_years_remaining_major` count."""
    if not years_remaining_major or season_start_year is None:
        return None
    last_start = int(season_start_year) + int(years_remaining_major) - 1
    y2 = (last_start + 1) % 100
    return f"{last_start}–{y2:02d}"


def contract_final_season_label(
    fhm_player_id: str | None,
    season_start_year: int | None,
    raw_import_dir: Path | None = None,
) -> str | None:
    """Last NHL season label covered by the contract from the league timeline (e.g. ``2038–39``).

    Uses the same ``major_YYYY`` walk as :func:`contract_years_remaining_major`.
    """
    n = contract_years_remaining_major(fhm_player_id, season_start_year, raw_import_dir)
    return contract_final_season_label_from_remaining(n, season_start_year)
