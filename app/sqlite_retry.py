"""SQLite commit retries for web requests (bot pollers + admin writes)."""
from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

_T = TypeVar("_T")

_DEFAULT_ATTEMPTS = 16
_DEFAULT_BASE_DELAY = 0.3


def _is_sqlite_locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "database is locked" in msg or "locked" in msg:
        return True
    orig = getattr(exc, "orig", None)
    return orig is not None and "locked" in str(orig).lower()


def _reset_session_after_lock(session: Session) -> None:
    session.rollback()
    try:
        session.expire_all()
    except Exception:
        pass


def _retry_sqlite_operation(
    session: Session,
    operation: Callable[[], _T],
    *,
    attempts: int,
    base_delay: float,
) -> _T:
    last: OperationalError | None = None
    for i in range(attempts):
        try:
            return operation()
        except OperationalError as exc:
            if not _is_sqlite_locked(exc):
                raise
            last = exc
            _reset_session_after_lock(session)
            if i >= attempts - 1:
                raise
            time.sleep(base_delay * (i + 1))
    if last is not None:
        raise last
    raise RuntimeError("SQLite retry exhausted without result")


def commit_with_sqlite_retry(
    session: Session,
    *,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
) -> None:
    """Commit, retrying on SQLite lock errors."""
    _retry_sqlite_operation(session, session.commit, attempts=attempts, base_delay=base_delay)


def flush_with_sqlite_retry(
    session: Session,
    *,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
) -> None:
    """Flush pending ORM state, retrying on SQLite lock errors."""
    _retry_sqlite_operation(session, session.flush, attempts=attempts, base_delay=base_delay)


def write_with_sqlite_retry(
    session: Session,
    write: Callable[[], _T],
    *,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
) -> _T:
    """Run a write callable and commit, retrying on SQLite lock errors."""

    def _write_and_commit() -> _T:
        result = write()
        session.commit()
        return result

    return _retry_sqlite_operation(
        session, _write_and_commit, attempts=attempts, base_delay=base_delay
    )
