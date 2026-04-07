"""Distinct season years from FHM career CSVs in the raw import directory."""
from __future__ import annotations

import csv
from pathlib import Path

from app.config import Config


def _detect_delimiter(sample_line: str) -> str:
    return ";" if sample_line.count(";") > sample_line.count(",") else ","


def _encoding_for_path(path: Path) -> str:
    try:
        from charset_normalizer import from_path

        result = from_path(str(path)).best()
        return result.encoding if result else "utf-8"
    except Exception:
        return "utf-8"


def distinct_years_from_career_csvs(raw_dir: Path | None = None) -> list[int]:
    """Return sorted unique ``Year`` values from ``*career_stats*.csv`` files."""
    base = Path(raw_dir) if raw_dir is not None else Config.RAW_IMPORT_DIR
    if not base.is_dir():
        return []
    years: set[int] = set()
    for path in sorted(base.glob("*career_stats*.csv")):
        if not path.is_file():
            continue
        try:
            enc = _encoding_for_path(path)
            with path.open(encoding=enc, errors="replace", newline="") as f:
                first = f.readline()
                if not first:
                    continue
                delim = _detect_delimiter(first)
                f.seek(0)
                reader = csv.DictReader(f, delimiter=delim)
                if not reader.fieldnames:
                    continue
                year_key = next(
                    (k for k in reader.fieldnames if k and str(k).strip().lower() == "year"),
                    None,
                )
                if not year_key:
                    continue
                for row in reader:
                    v = row.get(year_key)
                    if v is None or str(v).strip() == "":
                        continue
                    try:
                        y = int(float(str(v).strip()))
                    except (TypeError, ValueError):
                        continue
                    if 1800 <= y <= 2200:
                        years.add(y)
        except OSError:
            continue
    return sorted(years)


def hockey_season_label(start_year: int) -> str:
    """e.g. 1966 -> ``1966-67``, 1999 -> ``1999-00``."""
    end = start_year + 1
    return f"{start_year}-{end % 100:02d}"


def import_folder_season_labels(raw_dir: Path | None = None) -> list[str]:
    """Human-readable season labels derived from career CSV ``Year`` columns."""
    return [hockey_season_label(y) for y in distinct_years_from_career_csvs(raw_dir)]
