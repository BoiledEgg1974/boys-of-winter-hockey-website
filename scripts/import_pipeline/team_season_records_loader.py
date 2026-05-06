"""Import the per-league ``team_season_records_template.csv`` into ``TeamSeasonRecord``.

Columns mirror the CSV. Blank cells become ``NULL``; the literal token ``"null"``
(case-insensitive) also becomes ``NULL`` but its origin is recorded in
``null_columns_csv`` so detail tables can render ``-`` for those cells while
leaderboards skip them entirely. Rows with no resolvable team and no
``Team Name Override`` are skipped with a log warning.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from sqlalchemy import delete, select

from app.models import Team, TeamSeasonRecord, db
from scripts.import_pipeline.encoding_utils import (
    cell_val,
    read_csv_normalized,
    to_float,
    to_int,
)

log = logging.getLogger("bowl.team_season_records")

CSV_FILENAME = "team_season_records_template.csv"

# (model_attr, list of normalized header keys to probe in order, parser).
# Header normalization in :func:`encoding_utils.normalize_header` turns e.g.
# "T (OTL)" -> "t_(otl)" and "PIM/G" -> "pim_g" — keep both literal and
# alternative spellings so the loader works across all three league CSVs.
_INT = "int"
_FLOAT = "float"
_TEXT = "text"

_FIELD_SPECS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("gp", ("gp",), _INT),
    ("w", ("w",), _INT),
    ("l", ("l",), _INT),
    ("t_otl", ("t_(otl)", "t_otl", "t"), _INT),
    ("pts", ("pts",), _INT),
    ("gf", ("gf",), _INT),
    ("ga", ("ga",), _INT),
    ("goal_diff", ("goal_differential", "goal_diff"), _INT),
    ("result", ("result",), _TEXT),
    ("pim_per_game", ("pim_g", "pim_per_game"), _FLOAT),
    ("ppg", ("ppg",), _INT),
    ("ppg_against", ("ppg_against",), _INT),
    ("pp_chances", ("pp_ch", "pp_chances", "ppc"), _INT),
    ("shg", ("shg",), _INT),
    ("shg_against", ("shg_against",), _INT),
    ("sh_chances", ("shc", "sh_chances", "sh_ch"), _INT),
    ("pp_pct", ("pp_pct",), _FLOAT),
    ("pk_pct", ("pk_pct",), _FLOAT),
    ("shots_for", ("shots_for", "shots"), _INT),
    ("shots_against", ("shots_against",), _INT),
)


def _is_null_token(raw: str | None) -> bool:
    if raw is None:
        return False
    return str(raw).strip().lower() == "null"


def _raw_cell(row: dict, *keys: str) -> str | None:
    """Return the raw cell value as-typed (no strip), to distinguish blank vs. ``"null"``."""
    for k in keys:
        if k in row:
            v = row[k]
            if v is None:
                continue
            return str(v)
    return None


def _resolve_team(team_id_csv: str | None) -> Team | None:
    if not team_id_csv:
        return None
    key = team_id_csv.strip()
    if not key:
        return None
    return db.session.scalars(select(Team).where(Team.fhm_team_id == key).limit(1)).first()


def _label_start_year(label: str | None) -> int | None:
    if not label:
        return None
    m = re.search(r"(\d{4})", str(label))
    return int(m.group(1)) if m else None


def import_team_season_records(raw_dir: Path, app) -> int:
    """Replace all rows in ``team_season_records`` from ``team_season_records_template.csv``."""
    path = raw_dir / CSV_FILENAME
    if not path.exists():
        log.info("Skipping %s (not found in %s).", CSV_FILENAME, raw_dir)
        return 0

    db.session.execute(delete(TeamSeasonRecord))
    db.session.commit()

    df = read_csv_normalized(path)
    available_cols = set(df.columns)
    n = 0
    skipped = 0

    for _, row in df.iterrows():
        r = row.to_dict()
        year_label = cell_val(r, "year")
        if not year_label:
            continue

        team_id_csv = cell_val(r, "team_id")
        team_name_override = cell_val(r, "team_name_override")
        team = _resolve_team(team_id_csv)
        if team is None and not team_name_override:
            skipped += 1
            log.warning(
                "Skipped %s row (year=%s) with no resolvable team and no Team Name Override.",
                CSV_FILENAME,
                year_label,
            )
            continue

        rec = TeamSeasonRecord(
            season_year_label=year_label,
            start_year=_label_start_year(year_label),
            team_id=team.id if team is not None else None,
            team_fhm_id_csv=team_id_csv,
            team_name_override=team_name_override,
            conference_id=to_int(cell_val(r, "conference_id")) if "conference_id" in available_cols else None,
            conference_override=cell_val(r, "conference_override"),
            division_id=to_int(cell_val(r, "division_id")) if "division_id" in available_cols else None,
            division_override=cell_val(r, "division_override"),
            logo_file_override=cell_val(r, "logo_file_override"),
        )

        null_cols: list[str] = []
        for attr, keys, kind in _FIELD_SPECS:
            available = any(k in available_cols for k in keys)
            if not available:
                setattr(rec, attr, None)
                continue
            raw = _raw_cell(r, *keys)
            if _is_null_token(raw):
                setattr(rec, attr, None)
                null_cols.append(attr)
                continue
            cleaned = cell_val(r, *keys)
            if cleaned is None:
                setattr(rec, attr, None)
                continue
            if kind == _INT:
                setattr(rec, attr, to_int(cleaned))
            elif kind == _FLOAT:
                setattr(rec, attr, to_float(cleaned))
            else:
                setattr(rec, attr, cleaned)

        rec.null_columns_csv = ",".join(null_cols) if null_cols else None
        db.session.add(rec)
        n += 1
        if n % 400 == 0:
            db.session.commit()

    db.session.commit()
    log.info(
        "Imported %s rows from %s (skipped %s rows with no resolvable team).",
        n,
        CSV_FILENAME,
        skipped,
    )
    return n
