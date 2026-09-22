"""Mount BOWL Perfect Squad under ``/<league-slug>/perfect-squad/`` on hockey league apps."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Any, Callable

from werkzeug.middleware.dispatcher import DispatcherMiddleware

from app.config import HOCKEY_LEAGUE_SLUGS

_MOUNT_PREFIX = "/perfect-squad"
# Per league: Flask app, snapshot of Perfect Squad ``app.*`` modules, repo root path.
_ps_runtime: dict[str, tuple[Any, dict[str, Any], str]] = {}
_ps_locks: dict[str, threading.Lock] = {}
_ps_lock_guard = threading.Lock()


def perfect_squad_root() -> Path | None:
    raw = os.environ.get("PERFECT_SQUAD_ROOT", "").strip()
    if raw:
        root = Path(raw).expanduser()
        if (root / "app" / "__init__.py").is_file():
            return root
        return None
    # Local dev fallback (OneDrive Desktop layout).
    desktop = Path.home() / "OneDrive" / "Desktop" / "BOWL Perfect Squad"
    if (desktop / "app" / "__init__.py").is_file():
        return desktop
    return None


def perfect_squad_enabled() -> bool:
    return perfect_squad_root() is not None


def perfect_squad_home_href(league_slug: str) -> str | None:
    if league_slug not in HOCKEY_LEAGUE_SLUGS or not perfect_squad_enabled():
        return None
    return f"/{league_slug}{_MOUNT_PREFIX}/{league_slug}/"


def _lock_for(slug: str) -> threading.Lock:
    with _ps_lock_guard:
        lock = _ps_locks.get(slug)
        if lock is None:
            lock = threading.Lock()
            _ps_locks[slug] = lock
        return lock


def _snapshot_app_modules() -> dict[str, Any]:
    return {key: sys.modules[key] for key in list(sys.modules) if key == "app" or key.startswith("app.")}


def _clear_app_modules() -> None:
    for key in list(sys.modules):
        if key == "app" or key.startswith("app."):
            sys.modules.pop(key, None)


def _restore_app_modules(saved: dict[str, Any]) -> None:
    _clear_app_modules()
    sys.modules.update(saved)


def _build_perfect_squad_app(league_slug: str):
    root = perfect_squad_root()
    if root is None:
        raise RuntimeError("PERFECT_SQUAD_ROOT is not configured or invalid")

    saved_modules = _snapshot_app_modules()
    ps_root = str(root.resolve())
    inserted = False
    try:
        _clear_app_modules()
        if ps_root not in sys.path:
            sys.path.insert(0, ps_root)
            inserted = True

        from app import create_app as ps_create_app  # noqa: WPS433

        bowl_repo = Path(__file__).resolve().parent.parent
        site_uri = os.environ.get("SITE_DATABASE_URL", "").strip()
        if site_uri:
            try:
                from app.config import normalize_site_database_url as _norm_site_db

                site_uri = _norm_site_db(site_uri)
            except ImportError:
                pass
        ps_config: dict[str, Any] = {
            "PS_MOUNT_LEAGUE_SLUG": league_slug,
            "SECRET_KEY": os.environ.get("SECRET_KEY"),
            "SESSION_COOKIE_NAME": os.environ.get("SESSION_COOKIE_NAME", "session"),
            "SESSION_COOKIE_PATH": "/",
            "BOWL_ROOT": str(bowl_repo),
        }
        if site_uri:
            ps_config["SITE_DATABASE_URL"] = site_uri
        ps_db = os.environ.get("PERFECT_SQUAD_DATABASE_URL", "").strip()
        if ps_db:
            ps_config["SQLALCHEMY_DATABASE_URI"] = ps_db
        if os.environ.get("DEV_BYPASS_LOGIN", "").strip().lower() in ("1", "true", "yes"):
            ps_config["DEV_BYPASS_LOGIN"] = True
            ps_config["DEV_BYPASS_USER_ID"] = int(os.environ.get("DEV_BYPASS_USER_ID", "1") or 1)
        app = ps_create_app(ps_config)
        ps_modules = _snapshot_app_modules()
        return app, ps_modules, ps_root
    finally:
        if inserted:
            try:
                sys.path.remove(ps_root)
            except ValueError:
                pass
        _restore_app_modules(saved_modules)


def _get_or_build_ps_runtime(league_slug: str) -> tuple[Any, dict[str, Any], str]:
    runtime = _ps_runtime.get(league_slug)
    if runtime is not None:
        return runtime
    runtime = _build_perfect_squad_app(league_slug)
    _ps_runtime[league_slug] = runtime
    return runtime


def _invoke_ps_wsgi(
    league_slug: str,
    app: Any,
    ps_modules: dict[str, Any],
    ps_root: str,
    environ,
    start_response,
):
    saved_bowl = _snapshot_app_modules()
    path_inserted = False
    try:
        _restore_app_modules(ps_modules)
        if ps_root not in sys.path:
            sys.path.insert(0, ps_root)
            path_inserted = True
        return app.wsgi_app(environ, start_response)
    finally:
        ps_modules = _snapshot_app_modules()
        _restore_app_modules(saved_bowl)
        if path_inserted:
            try:
                sys.path.remove(ps_root)
            except ValueError:
                pass
        _ps_runtime[league_slug] = (app, ps_modules, ps_root)


def _lazy_perfect_squad_wsgi(league_slug: str) -> Callable:
    def application(environ, start_response):
        lock = _lock_for(league_slug)
        with lock:
            app, ps_modules, ps_root = _get_or_build_ps_runtime(league_slug)
            return _invoke_ps_wsgi(league_slug, app, ps_modules, ps_root, environ, start_response)

    return application


def wrap_league_wsgi_with_perfect_squad(league_wsgi_app: Callable, league_slug: str) -> Callable:
    """Nest Perfect Squad under ``/perfect-squad`` for hockey league mounts."""
    if league_slug not in HOCKEY_LEAGUE_SLUGS or not perfect_squad_enabled():
        return league_wsgi_app
    return DispatcherMiddleware(league_wsgi_app, {_MOUNT_PREFIX: _lazy_perfect_squad_wsgi(league_slug)})
