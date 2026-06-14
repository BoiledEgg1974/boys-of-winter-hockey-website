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


def commit_with_sqlite_retry(
    session: Session,
    *,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
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
            _reset_session_after_lock(session)
            if i >= attempts - 1:
                raise
            time.sleep(base_delay * (i + 1))
    if last is not None:
        raise last


def write_with_sqlite_retry(
    session: Session,
    write: Callable[[], _T],
    *,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
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
            _reset_session_after_lock(session)
            if i >= attempts - 1:
                raise
            time.sleep(base_delay * (i + 1))
    if last is not None:
        raise last
    raise RuntimeError("write_with_sqlite_retry exhausted without result")
