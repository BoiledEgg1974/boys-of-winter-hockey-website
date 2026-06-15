"""Tests for SQLite connection PRAGMAs."""
from __future__ import annotations

import tempfile
import unittest
import os
from pathlib import Path

from sqlalchemy import create_engine, text

from app.sqlite_pragmas import install_sqlite_connect_pragmas


class SqlitePragmaTests(unittest.TestCase):
    def test_sqlite_connections_use_wal_and_short_busy_timeout(self) -> None:
        install_sqlite_connect_pragmas()
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "pragma-test.db"
            engine = create_engine(f"sqlite:///{db_path}")
            try:
                with engine.connect() as conn:
                    journal_mode = conn.execute(text("PRAGMA journal_mode")).scalar()
                    busy_timeout = conn.execute(text("PRAGMA busy_timeout")).scalar()
            finally:
                engine.dispose()

        self.assertEqual(str(journal_mode).lower(), "wal")
        expected_busy_timeout = int(float(os.environ.get("SQLITE_BUSY_TIMEOUT_SECONDS", "12")) * 1000)
        self.assertEqual(int(busy_timeout), expected_busy_timeout)


if __name__ == "__main__":
    unittest.main()
