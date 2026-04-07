"""Import or refresh skater career lines from FHM raw CSVs (rs / po / retired variants).

Run from repo root: python scripts/import_skater_career_csvs.py

Uses the same upsert logic as scripts.import_pipeline.fhm_loader.import_career_skater_file.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select

from app import create_app
from app.models import Player, Team, db
from app.config import Config
from scripts.import_pipeline.fhm_loader import import_career_skater_file
from scripts.migrate_skater_career_unique import main as migrate_skater_career_unique


def _players_fhm_map() -> dict[int, int]:
    out: dict[int, int] = {}
    for p in db.session.scalars(select(Player)):
        if not p.fhm_player_id:
            continue
        try:
            out[int(str(p.fhm_player_id).strip())] = p.id
        except ValueError:
            continue
    return out


def _teams_fhm_map() -> dict[int, int]:
    out: dict[int, int] = {}
    for t in db.session.scalars(select(Team)):
        if not t.fhm_team_id:
            continue
        try:
            out[int(str(t.fhm_team_id).strip())] = t.id
        except ValueError:
            continue
    return out


def main() -> None:
    migrate_skater_career_unique()
    raw_dir = Config.RAW_IMPORT_DIR
    app = create_app()
    with app.app_context():
        players_fhm = _players_fhm_map()
        teams_fhm = _teams_fhm_map()
        files = [
            ("player_skater_career_stats_rs.csv", "rs"),
            ("player_skater_career_stats_po.csv", "po"),
            ("player_skater_retired_career_stats_rs.csv", "retired_rs"),
            ("player_skater_retired_career_stats_ps.csv", "retired_ps"),
            ("player_skater_retired_career_stats_po.csv", "retired_po"),
        ]
        for fname, src in files:
            path = raw_dir / fname
            if not path.is_file():
                print(f"skip (missing): {fname}")
                continue
            n = import_career_skater_file(raw_dir, fname, src, players_fhm, teams_fhm)
            print(f"{fname} ({src}): {n} rows processed")


if __name__ == "__main__":
    main()
