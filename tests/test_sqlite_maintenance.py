"""SQLite integrity and player_rating_snapshots repair helpers."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db_utils import (
    reset_player_rating_snapshots_sqlite,
    sqlite_integrity_message,
    sqlite_is_healthy,
)


class SqliteMaintenanceTest(unittest.TestCase):
    def test_integrity_ok_on_fresh_db(self) -> None:
        db_path = Path(self._fresh_db())
        self.assertTrue(sqlite_is_healthy(db_path))
        self.assertEqual(sqlite_integrity_message(db_path), "ok")

    def test_reset_player_rating_snapshots_recreates_table(self) -> None:
        db_path = Path(self._fresh_db())
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.connect() as conn:
            conn.execute(
                text(
                    """
                    CREATE TABLE player_rating_snapshots (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        player_id INTEGER NOT NULL,
                        league_slug VARCHAR(64) NOT NULL,
                        snapshot_at DATETIME NOT NULL,
                        ratings_json TEXT NOT NULL
                    )
                    """
                )
            )
            conn.commit()
        reset_player_rating_snapshots_sqlite(engine)
        with engine.connect() as conn:
            cols = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(player_rating_snapshots)"))
            }
        engine.dispose()
        self.assertIn("ability", cols)
        self.assertIn("overall_score", cols)

    def _fresh_db(self) -> str:
        import tempfile

        fd, name = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        conn = sqlite3.connect(name)
        conn.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        self.addCleanup(lambda: Path(name).unlink(missing_ok=True))
        return name


if __name__ == "__main__":
    unittest.main()
