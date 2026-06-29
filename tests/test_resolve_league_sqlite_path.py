"""League SQLite path resolution prefers valid legacy files over corrupt primaries."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from app.config import resolve_league_sqlite_path


class ResolveLeagueSqlitePathTests(unittest.TestCase):
    def test_corrupt_primary_falls_back_to_legacy(self) -> None:
        inst = Path(__file__).resolve().parents[1] / "instance"
        inst.mkdir(parents=True, exist_ok=True)
        primary = inst / "bowl-historical.db"
        legacy = inst / "league2.db"
        primary_backup = inst / "bowl-historical.db.test-bak"
        legacy_backup = inst / "league2.db.test-bak"
        for path in (primary, legacy, primary_backup, legacy_backup):
            if path.is_file():
                path.rename(path.with_suffix(path.suffix + ".test-bak"))
        try:
            primary.write_bytes(b"not-a-sqlite-database-file")
            legacy_conn = sqlite3.connect(str(legacy))
            try:
                legacy_conn.execute(
                    "CREATE TABLE teams (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
                )
                legacy_conn.execute("INSERT INTO teams (id, name) VALUES (1, 'Test')")
                legacy_conn.commit()
            finally:
                legacy_conn.close()

            chosen = resolve_league_sqlite_path("bowl-historical")
            self.assertEqual(chosen.resolve(), legacy.resolve())
        finally:
            for path in (primary, legacy):
                if path.is_file():
                    path.unlink()
            for backup in (primary_backup, legacy_backup):
                if backup.is_file():
                    backup.rename(backup.with_suffix("").with_suffix(".db"))


if __name__ == "__main__":
    unittest.main()
