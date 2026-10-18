"""Distinct season years from FHM career CSVs in the raw import directory."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from app.config import Config
from scripts.import_pipeline.encoding_utils import decode_fhm_csv_bytes, detect_delimiter


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
            text, _enc = decode_fhm_csv_bytes(path.read_bytes())
        except OSError:
            continue
        if not text:
            continue
        delim = detect_delimiter(text)
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
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
    return sorted(years)


def hockey_season_label(start_year: int) -> str:
    """e.g. 1966 -> ``1966-67``, 1999 -> ``1999-00``."""
    end = start_year + 1
    return f"{start_year}-{end % 100:02d}"


def import_folder_season_labels(raw_dir: Path | None = None) -> list[str]:
    """Human-readable season labels derived from career CSV ``Year`` columns."""
    return [hockey_season_label(y) for y in distinct_years_from_career_csvs(raw_dir)]
