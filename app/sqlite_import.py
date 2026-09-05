"""SQLite tuning for long-running CSV imports (disk-constrained hosts)."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError

from app.db_utils import sqlite_wal_checkpoint

_log = logging.getLogger(__name__)


def configure_sqlite_for_bulk_import(engine: Engine, *, db_path: Path | None = None) -> None:
    """Checkpoint WAL sidecars and use rollback-journal mode for heavy imports.

    WAL mode keeps ``.db-wal`` / ``.db-shm`` siblings that can double peak disk use
    on small hosts (e.g. PythonAnywhere). Web workers switch back to WAL on reconnect.

    ``PRAGMA journal_mode=DELETE`` needs an exclusive lock. A local ``python run.py``,
    Discord bot, or leftover pool connection will raise ``database is locked``. Retry,
    then continue in WAL rather than aborting the import.
    """
    if engine.dialect.name != "sqlite":
        return
    engine.dispose()
    if db_path is not None:
        sqlite_wal_checkpoint(db_path)

    attempts = 6
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        try:
            with engine.connect() as conn:
                conn.execute(text("PRAGMA busy_timeout=60000"))
                mode = conn.execute(text("PRAGMA journal_mode=DELETE")).scalar()
                conn.execute(text("PRAGMA synchronous=NORMAL"))
                conn.commit()
            _log.info("SQLite bulk-import mode: journal_mode=%s", mode)
            return
        except OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower():
                raise
            engine.dispose()
            time.sleep(0.35 * (attempt + 1))

    _log.warning(
        "Could not switch SQLite to DELETE journal mode (%s). Continuing in WAL. "
        "Close local `python run.py` or other apps using this database if imports keep locking.",
        last_exc,
    )
