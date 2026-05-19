"""URL helpers for league apps mounted under ``/<league-slug>/``."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from flask import Flask
from werkzeug.local import LocalProxy


def real_flask_app(app: Flask) -> Flask:
    """Resolve ``current_app`` (or any proxy) to the underlying Flask instance."""
    if isinstance(app, LocalProxy):
        return app._get_current_object()  # type: ignore[return-value]
    return app


def league_mount_prefix(app: Flask | None = None) -> str:
    """Path prefix for this league (e.g. ``/bowl-fantasy``), or ``''`` at domain root."""
    from flask import current_app, has_request_context, request

    app = app or current_app
    if has_request_context():
        root = (request.script_root or "").strip()
        if root and root != "/":
            return root.rstrip("/")
    slug = str(app.config.get("LEAGUE_SLUG") or "").strip().strip("/")
    if slug:
        return f"/{slug}"
    configured = str(app.config.get("APPLICATION_ROOT") or "").strip().rstrip("/")
    return configured if configured and configured != "/" else ""


def prefix_league_static_urls(value: Any, *, prefix: str | None = None, app: Flask | None = None) -> Any:
    """Rewrite ``/static/...`` strings to include the league mount prefix when needed."""
    mount = (prefix if prefix is not None else league_mount_prefix(app)).rstrip("/")
    if not mount:

        def _walk(v: Any) -> Any:
            if isinstance(v, dict):
                return {k: _walk(x) for k, x in v.items()}
            if isinstance(v, list):
                return [_walk(x) for x in v]
            return v

        return _walk(value)

    marker = f"{mount}/static/"

    def _walk(v: Any) -> Any:
        if isinstance(v, dict):
            return {k: _walk(x) for k, x in v.items()}
        if isinstance(v, list):
            return [_walk(x) for x in v]
        if isinstance(v, str) and v.startswith("/static/") and not v.startswith(marker):
            return mount + v
        return v

    return _walk(value)


@contextmanager
def league_test_request_context(app: Flask) -> Iterator[Flask]:
    """App + request context with correct ``SCRIPT_NAME`` for mounted leagues."""
    bound = real_flask_app(app)
    slug = str(bound.config.get("LEAGUE_SLUG") or "").strip().strip("/")
    mount = f"/{slug}" if slug else ""
    with bound.app_context():
        with bound.test_request_context(path="/", base_url=f"http://127.0.0.1{mount}/"):
            yield bound
