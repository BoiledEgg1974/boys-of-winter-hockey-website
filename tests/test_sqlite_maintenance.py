"""SQLite integrity and player_rating_snapshots repair helpers."""
from __future__ import annotations

import sqlite3
import unittest
from pathlib import Path

from sqlalchemy import create_engine, text

from app.db_utils import (
    prepare_sqlite_database,
    rebuild_player_fts,
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

    def test_prepare_sqlite_database_ok_without_repair(self) -> None:
        db_path = Path(self._fresh_db())
        healthy, msg = prepare_sqlite_database(db_path, auto_repair=False)
        self.assertTrue(healthy)
        self.assertEqual(msg, "ok")

    def test_rebuild_player_fts_recovers_corrupt_vtable(self) -> None:
        db_path = Path(self._fresh_db())
        engine = create_engine(f"sqlite:///{db_path.as_posix()}")
        with engine.connect() as conn:
            conn.execute(
                text(
                    "CREATE TABLE players (id INTEGER PRIMARY KEY, full_name TEXT, position TEXT, current_team_id INTEGER)"
                )
            )
            conn.execute(text("ALTER TABLE teams ADD COLUMN abbreviation TEXT"))
            conn.execute(text("INSERT INTO players (id, full_name, position) VALUES (1, 'Wayne Gretzky', 'C')"))
            conn.commit()
        rebuild_player_fts(engine)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("PRAGMA writable_schema=ON")
            conn.execute("DELETE FROM sqlite_master WHERE name='player_fts_config'")
            conn.execute("PRAGMA writable_schema=OFF")
            conn.commit()
        with sqlite3.connect(str(db_path)) as conn:
            with self.assertRaises(sqlite3.DatabaseError):
                conn.execute("DROP TABLE IF EXISTS player_fts")
        rebuild_player_fts(engine)
        with engine.connect() as conn:
            count = conn.execute(text("SELECT COUNT(*) FROM player_fts")).scalar()
        engine.dispose()
        self.assertEqual(count, 1)

    def test_prepare_sqlite_database_reports_unhealthy_without_repair(self) -> None:
        db_path = Path(self._fresh_db())
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE corrupt_probe (id INTEGER PRIMARY KEY, v TEXT)")
            conn.executemany(
                "INSERT INTO corrupt_probe (v) VALUES (?)",
                [(f"row-{i}",) for i in range(200)],
            )
            conn.commit()
        size = db_path.stat().st_size
        with db_path.open("r+b") as fh:
            fh.seek(max(0, size - 4096))
            fh.write(b"\x00" * 512)
        self.assertFalse(sqlite_is_healthy(db_path))
        healthy, msg = prepare_sqlite_database(db_path, auto_repair=False)
        self.assertFalse(healthy)
        self.assertNotEqual(msg.lower(), "ok")

    def _fresh_db(self) -> str:
        import tempfile

        fd, name = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        conn = sqlite3.connect(name)
        conn.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        db_path = Path(name)

        def _cleanup() -> None:
            for path in (db_path, Path(f"{name}-wal"), Path(f"{name}-shm")):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass

        self.addCleanup(_cleanup)
        return name


if __name__ == "__main__":
    unittest.main()
