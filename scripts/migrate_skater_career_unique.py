"""Fix SQLite schema: skater career unique key must include career_source (rs vs po).

Without this, playoff lines cannot coexist with regular-season lines for the same
season/team/league. Run once: python scripts/migrate_skater_career_unique.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_slug = os.environ.get("LEAGUE_SLUG", "bowl-fantasy")
DB = ROOT / "instance" / f"{_slug}.db"


def main() -> None:
    if not DB.is_file():
        print(f"No database at {DB}", file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(DB)
    sql = conn.execute(
        "select sql from sqlite_master where type='table' and name='player_skater_career_lines'"
    ).fetchone()
    if not sql or sql[0] is None:
        print("player_skater_career_lines missing", file=sys.stderr)
        sys.exit(1)
    ddl = sql[0]
    if re.search(r"UNIQUE\s*\([^)]*career_source[^)]*\)", ddl, re.DOTALL | re.IGNORECASE):
        print("Schema already has career_source in unique constraint; nothing to do.")
        return

    print("Rebuilding player_skater_career_lines with correct UNIQUE constraint...")
    conn.execute("BEGIN")
    conn.executescript(
        """
        CREATE TABLE player_skater_career_lines__new (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            player_id INTEGER NOT NULL,
            season_year INTEGER NOT NULL,
            team_fhm_id INTEGER NOT NULL,
            league_fhm_id INTEGER NOT NULL,
            career_source VARCHAR(24) NOT NULL DEFAULT 'rs',
            team_id INTEGER,
            gp INTEGER NOT NULL,
            goals INTEGER NOT NULL,
            assists INTEGER NOT NULL,
            pim INTEGER NOT NULL,
            plus_minus INTEGER,
            pp_goals INTEGER,
            pp_assists INTEGER,
            sh_goals INTEGER,
            sh_assists INTEGER,
            shots INTEGER,
            hits INTEGER,
            fights INTEGER,
            fights_won INTEGER,
            FOREIGN KEY(player_id) REFERENCES players (id),
            FOREIGN KEY(team_id) REFERENCES teams (id),
            CONSTRAINT uq_career_skater_line UNIQUE (
                player_id, season_year, team_fhm_id, league_fhm_id, career_source
            )
        );
        INSERT INTO player_skater_career_lines__new (
            id, player_id, season_year, team_fhm_id, league_fhm_id, career_source,
            team_id, gp, goals, assists, pim, plus_minus, pp_goals, pp_assists,
            sh_goals, sh_assists, shots, hits, fights, fights_won
        )
        SELECT
            id, player_id, season_year, team_fhm_id, league_fhm_id, career_source,
            team_id, gp, goals, assists, pim, plus_minus, pp_goals, pp_assists,
            sh_goals, sh_assists, shots, hits, fights, fights_won
        FROM player_skater_career_lines;
        DROP TABLE player_skater_career_lines;
        ALTER TABLE player_skater_career_lines__new RENAME TO player_skater_career_lines;
        """
    )
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
