"""Shared Flask-SQLAlchemy handle: league tables (default bind) + site tables (``site`` bind)."""
from __future__ import annotations

from typing import Any

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.orm import Session

db = SQLAlchemy()


def commit_or_release_after_tick(session: Session, refresh_row: Any | None = None) -> None:
    """After ``process_tick``: commit if anything changed; else rollback idle txn.

    SQLite otherwise keeps a read transaction open while building JSON, which
    increases ``database is locked`` errors under concurrent pollers + admin POSTs.
    """
    if session.new or session.dirty or session.deleted:
        session.commit()
        if refresh_row is not None:
            session.refresh(refresh_row)
    else:
        session.rollback()
