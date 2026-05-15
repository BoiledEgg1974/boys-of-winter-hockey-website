"""SQLite connection tuning for web concurrency (WAL + busy timeout)."""
from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.engine import Engine

_installed = False


def install_sqlite_connect_pragmas() -> None:
    """Register once: WAL journal and busy_timeout on every new SQLite connection."""
    global _installed
    if _installed:
        return

    @event.listens_for(Engine, "connect")
    def _sqlite_pragmas(dbapi_conn, connection_record):  # noqa: ANN001
        engine = getattr(connection_record, "engine", None)
        if engine is None or engine.dialect.name != "sqlite":
            return
        cur = dbapi_conn.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=60000")
        finally:
            cur.close()

    _installed = True
