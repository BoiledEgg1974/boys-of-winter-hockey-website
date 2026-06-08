"""SQLite commit retries for web requests (bot pollers + admin writes)."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

_T = TypeVar("_T")


def _is_sqlite_locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "database is locked" in msg or "locked" in msg:
        return True
    orig = getattr(exc, "orig", None)
    return orig is not None and "locked" in str(orig).lower()


def commit_with_sqlite_retry(
    session: Session,
    *,
    attempts: int = 8,
    base_delay: float = 0.15,
) -> None:
    """Commit, retrying on SQLite lock errors."""
    last: OperationalError | None = None
    for i in range(attempts):
        try:
            session.commit()
            return
        except OperationalError as exc:
            if not _is_sqlite_locked(exc):
                raise
            last = exc
            session.rollback()
            if i >= attempts - 1:
                raise
            time.sleep(base_delay * (i + 1))
    if last is not None:
        raise last


def write_with_sqlite_retry(
    session: Session,
    write: Callable[[], _T],
    *,
    attempts: int = 8,
    base_delay: float = 0.15,
) -> _T:
    """Run a write callable and commit, retrying on SQLite lock errors."""
    last: OperationalError | None = None
    for i in range(attempts):
        try:
            result = write()
            session.commit()
            return result
        except OperationalError as exc:
            if not _is_sqlite_locked(exc):
                raise
            last = exc
            session.rollback()
            if i >= attempts - 1:
                raise
            time.sleep(base_delay * (i + 1))
    if last is not None:
        raise last
    raise RuntimeError("write_with_sqlite_retry exhausted without result")
