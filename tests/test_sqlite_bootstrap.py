"""SQLite bootstrap skip markers and lock coordination."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.sqlite_bootstrap import (
    SQLITE_BOOTSTRAP_VERSION,
    _db_key,
    _marker_matches,
    _write_marker,
    run_sqlite_bootstrap_once,
)


class SqliteBootstrapOnceTests(unittest.TestCase):
    def test_skips_repeat_bootstrap_in_same_process(self) -> None:
        calls: list[int] = []

        def bootstrap() -> None:
            calls.append(1)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "league.db"
            db_uri = f"sqlite:///{db_path.as_posix()}"
            with patch("app.sqlite_bootstrap.sqlite_bootstrap_lock") as lock_ctx:
                lock_ctx.return_value.__enter__.return_value = None
                lock_ctx.return_value.__exit__.return_value = None
                run_sqlite_bootstrap_once(db_uri, bootstrap, label="test")
                run_sqlite_bootstrap_once(db_uri, bootstrap, label="test")

        self.assertEqual(calls, [1])

    def test_marker_skips_bootstrap_body(self) -> None:
        calls: list[int] = []

        def bootstrap() -> None:
            calls.append(1)

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "league.db"
            db_uri = f"sqlite:///{db_path.as_posix()}"
            db_key = _db_key(db_uri)
            assert db_key is not None
            _write_marker(db_key)
            self.assertTrue(_marker_matches(db_key))
            with patch("app.sqlite_bootstrap.sqlite_bootstrap_lock") as lock_ctx:
                lock_ctx.return_value.__enter__.return_value = None
                lock_ctx.return_value.__exit__.return_value = None
                run_sqlite_bootstrap_once(db_uri, bootstrap, label="test")

            self.assertEqual(calls, [])
            marker = Path(db_key + ".bootstrap.version")
            self.assertTrue(marker.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), str(SQLITE_BOOTSTRAP_VERSION))


if __name__ == "__main__":
    unittest.main()
