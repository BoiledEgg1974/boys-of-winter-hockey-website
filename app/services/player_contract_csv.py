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


def _contract_year_salary_int(row: dict, prefix: str, year: int) -> int | None:
    """Read ``major_YYYY`` / ``minor_YYYY`` from a normalized CSV row; negative = sentinel."""
    nrm = {str(k).lower(): v for k, v in row.items()}
    raw = nrm.get(f"{prefix}_{year}")
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def player_contract_salary_by_season(
    fhm_player_id: str | None,
    raw_import_dir: Path | None = None,
) -> list[dict[str, object]]:
    """Season / Level / Amount rows from ``player_contract.csv`` for the player page.

    Uses the same ``major_YYYY`` / ``minor_YYYY`` columns as cap sheets: NHL salary when
    ``major`` is non-negative, otherwise minors salary when ``minor`` is non-negative.
    """
    if not fhm_player_id or not str(fhm_player_id).strip():
        return []
    base = raw_import_dir if raw_import_dir is not None else Path(Config.RAW_IMPORT_DIR)
    path = base / "player_contract.csv"
    m = _contract_row_map(path)
    row = m.get(str(fhm_player_id).strip())
    if not row:
        return []
    years: set[int] = set()
    for k in row:
        ks = str(k).lower()
        for pref in ("major_", "minor_"):
            if ks.startswith(pref):
                suf = ks[len(pref) :]
                try:
                    years.add(int(suf))
                except ValueError:
                    pass
    if not years:
        return []
    out: list[dict[str, object]] = []
    for y in sorted(years):
        mv = _contract_year_salary_int(row, "major", y)
        nv = _contract_year_salary_int(row, "minor", y)
        val: int | None = None
        level: str | None = None
        if mv is not None and mv >= 0:
            val = mv
            level = "NHL"
        elif nv is not None and nv >= 0:
            val = nv
            level = "Minors"
        if val is None:
            continue
        y_end = (y + 1) % 100
        season_label = f"{y}/{y_end:02d}"
        out.append({"season_label": season_label, "level": level, "amount": int(val)})
    return out


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
