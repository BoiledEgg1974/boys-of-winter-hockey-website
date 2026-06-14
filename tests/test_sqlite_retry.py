"""Tests for web SQLite commit retry helper."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from app.sqlite_retry import commit_with_sqlite_retry, write_with_sqlite_retry


class SqliteRetryTests(unittest.TestCase):
    def test_commit_retries_locked_and_expires_session(self) -> None:
        session = MagicMock()
        locked = OperationalError("stmt", {}, Exception("database is locked"))
        session.commit.side_effect = [locked, None]

        commit_with_sqlite_retry(session, attempts=3, base_delay=0)

        self.assertEqual(session.commit.call_count, 2)
        session.rollback.assert_called_once()
        session.expire_all.assert_called_once()

    def test_write_retries_locked_commit(self) -> None:
        session = MagicMock()
        locked = OperationalError("stmt", {}, Exception("database is locked"))
        session.commit.side_effect = [locked, None]
        write = MagicMock(return_value=9)

        result = write_with_sqlite_retry(session, write, attempts=3, base_delay=0)

        self.assertEqual(result, 9)
        self.assertEqual(write.call_count, 2)
        session.expire_all.assert_called_once()


if __name__ == "__main__":
    unittest.main()
