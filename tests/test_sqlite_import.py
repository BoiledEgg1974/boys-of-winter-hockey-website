"""Bulk-import SQLite journal-mode helper."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError

from app.sqlite_import import configure_sqlite_for_bulk_import


class SqliteImportConfigTests(unittest.TestCase):
    def test_locked_journal_mode_does_not_abort_import(self) -> None:
        engine = MagicMock()
        engine.dialect.name = "sqlite"
        locked = OperationalError("PRAGMA journal_mode=DELETE", {}, Exception("database is locked"))
        conn = MagicMock()
        conn.execute.side_effect = locked
        engine.connect.return_value.__enter__.return_value = conn

        with patch("app.sqlite_import.time.sleep"):
            configure_sqlite_for_bulk_import(engine)

        self.assertGreaterEqual(engine.dispose.call_count, 1)
        self.assertGreaterEqual(engine.connect.call_count, 2)

    def test_non_lock_errors_still_raise(self) -> None:
        engine = MagicMock()
        engine.dialect.name = "sqlite"
        conn = MagicMock()
        conn.execute.side_effect = OperationalError(
            "PRAGMA journal_mode=DELETE", {}, Exception("no such table")
        )
        engine.connect.return_value.__enter__.return_value = conn

        with self.assertRaises(OperationalError) as ctx:
            configure_sqlite_for_bulk_import(engine)
        self.assertIn("no such table", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
