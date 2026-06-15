"""Serialize SQLite schema bootstrap across WSGI workers and threads."""
from __future__ import annotations

import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path

_thread_locks: dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


def _thread_lock(key: str) -> threading.Lock:
    with _thread_locks_guard:
        lock = _thread_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[key] = lock
        return lock


def _sqlite_db_path(db_uri: str) -> Path | None:
    if not isinstance(db_uri, str) or not db_uri.startswith("sqlite:///"):
        return None
    raw = db_uri[len("sqlite:///") :]
    if not raw:
        return None
    return Path(raw)


@contextmanager
def sqlite_bootstrap_lock(db_uri: str, *, timeout_s: float = 300.0):
    """Hold an exclusive lock while running league SQLite bootstrap."""
    path = _sqlite_db_path(db_uri)
    if path is None:
        yield
        return

    key = str(path.resolve())
    lock_path = path.with_name(path.name + ".bootstrap.lock")

    with _thread_lock(key):
        lock_file = None
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_file = open(lock_path, "a+b")

            if os.name != "nt":
                import fcntl

                deadline = time.monotonic() + timeout_s
                while True:
                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"Timed out waiting for bootstrap lock: {lock_path}") from None
                        time.sleep(0.25)

            yield
        finally:
            if lock_file is not None:
                if os.name != "nt":
                    import fcntl

                    try:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                lock_file.close()
