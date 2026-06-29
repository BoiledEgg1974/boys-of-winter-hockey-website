"""SQLite connection tuning for web concurrency (WAL + busy timeout)."""
from __future__ import annotations

import os
import sqlite3

from sqlalchemy import event
from sqlalchemy.engine import Engine

_installed = False
_BUSY_TIMEOUT_MS = int(float(os.environ.get("SQLITE_BUSY_TIMEOUT_SECONDS", "12")) * 1000)


def install_sqlite_connect_pragmas() -> None:
    """Register once: WAL journal and busy_timeout on every new SQLite connection."""
    global _installed
    if _installed:
        return

    @event.listens_for(Engine, "connect")
    def _sqlite_pragmas(dbapi_conn, connection_record):  # noqa: ANN001
        module_name = str(getattr(type(dbapi_conn), "__module__", "") or "").lower()
        if "sqlite" not in module_name:
            return
        cur = dbapi_conn.cursor()
        try:
            try:
                cur.execute("PRAGMA journal_mode=WAL")
            except (sqlite3.OperationalError, sqlite3.DatabaseError):
                # Corrupt or read-only DB — avoid failing every connection open.
                pass
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
        finally:
            cur.close()

    _installed = True
