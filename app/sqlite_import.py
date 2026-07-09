"""SQLite tuning for long-running CSV imports (disk-constrained hosts)."""
from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine

from app.db_utils import sqlite_wal_checkpoint

_log = logging.getLogger(__name__)


def configure_sqlite_for_bulk_import(engine: Engine, *, db_path: Path | None = None) -> None:
    """Checkpoint WAL sidecars and use rollback-journal mode for heavy imports.

    WAL mode keeps ``.db-wal`` / ``.db-shm`` siblings that can double peak disk use
    on small hosts (e.g. PythonAnywhere). Web workers switch back to WAL on reconnect.
    """
    if engine.dialect.name != "sqlite":
        return
    if db_path is not None:
        sqlite_wal_checkpoint(db_path)
    with engine.connect() as conn:
        mode = conn.execute(text("PRAGMA journal_mode=DELETE")).scalar()
        conn.execute(text("PRAGMA synchronous=NORMAL"))
        conn.commit()
    _log.info("SQLite bulk-import mode: journal_mode=%s", mode)
