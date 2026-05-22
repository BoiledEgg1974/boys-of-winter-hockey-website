"""SQLite helpers for long-running import scripts."""
from __future__ import annotations

import time

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def _is_sqlite_locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "database is locked" in msg or "locked" in msg:
        return True
    orig = getattr(exc, "orig", None)
    return orig is not None and "locked" in str(orig).lower()


def commit_with_sqlite_retry(
    session: Session,
    *,
    attempts: int = 12,
    base_delay: float = 0.25,
) -> None:
    """Commit, retrying on SQLite lock errors (web workers + import contention)."""
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
