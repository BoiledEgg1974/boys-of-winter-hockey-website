"""Tests for import SQLite commit retry helper."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from scripts.import_pipeline.sqlite_session import commit_with_sqlite_retry, write_with_sqlite_retry


def test_commit_with_sqlite_retry_succeeds_first_try():
    session = MagicMock()
    commit_with_sqlite_retry(session, attempts=3)
    session.commit.assert_called_once()
    session.rollback.assert_not_called()


def test_commit_with_sqlite_retry_retries_locked():
    session = MagicMock()
    locked = OperationalError("stmt", {}, Exception("database is locked"))
    session.commit.side_effect = [locked, None]
    commit_with_sqlite_retry(session, attempts=3, base_delay=0)
    assert session.commit.call_count == 2
    session.rollback.assert_called_once()


def test_commit_with_sqlite_retry_raises_non_lock_errors():
    session = MagicMock()
    session.commit.side_effect = OperationalError("stmt", {}, Exception("no such table"))
    with unittest.TestCase().assertRaises(OperationalError):
        commit_with_sqlite_retry(session, attempts=2, base_delay=0)


def test_write_with_sqlite_retry_retries_write_callable_after_locked_flush():
    session = MagicMock()
    locked = OperationalError("stmt", {}, Exception("database is locked"))
    write = MagicMock(side_effect=[locked, 7])

    result = write_with_sqlite_retry(session, write, attempts=3, base_delay=0)

    assert result == 7
    assert write.call_count == 2
    session.rollback.assert_called_once()
    session.commit.assert_called_once()


def test_write_with_sqlite_retry_retries_locked_commit():
    session = MagicMock()
    locked = OperationalError("stmt", {}, Exception("database is locked"))
    session.commit.side_effect = [locked, None]
    write = MagicMock(return_value=9)

    result = write_with_sqlite_retry(session, write, attempts=3, base_delay=0)

    assert result == 9
    assert write.call_count == 2
    assert session.commit.call_count == 2
    session.rollback.assert_called_once()
