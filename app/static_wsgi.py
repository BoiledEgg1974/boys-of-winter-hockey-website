"""Serve shared ``app/static`` files without loading league Flask apps (WSGI layer)."""
from __future__ import annotations

import mimetypes
from pathlib import Path


def static_relative_path(path_info: str, league_slugs: frozenset[str]) -> str | None:
    """Map ``/static/...`` or ``/<slug>/static/...`` to a path under ``app/static``."""
    path = str(path_info or "")
    if not path.startswith("/"):
        return None
    if path.startswith("/static/"):
        return path[len("/static/") :]
    parts = path.split("/")
    # ["", "bowl-cap", "static", "players", "foo.png"]
    if len(parts) >= 4 and parts[2] == "static":
        slug = parts[1]
        if slug in league_slugs:
            return "/".join(parts[3:])
    return None


def safe_static_file(static_root: Path, relative: str) -> Path | None:
    """Resolve ``relative`` under ``static_root``; reject traversal and missing files."""
    rel = str(relative or "").lstrip("/").replace("\\", "/")
    if not rel or rel.startswith("..") or "/../" in f"/{rel}/":
        return None
    root = static_root.resolve()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if candidate.is_file():
        return candidate
    return None


def _wsgi_file_response(path: Path, environ, start_response):
    stat = path.stat()
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    headers = [
        ("Content-Type", content_type),
        ("Content-Length", str(stat.st_size)),
        ("Cache-Control", "public, max-age=86400"),
    ]
    start_response("200 OK", headers)
    fobj = path.open("rb")
    wrapper = environ.get("wsgi.file_wrapper")
    if wrapper is not None:
        # PEP 3333: wsgi.file_wrapper(filelike, block_size) — uWSGI expects an int, not a mode.
        try:
            return wrapper(fobj, 8192)
        except TypeError:
            return wrapper(fobj)

    def _iter_and_close():
        try:
            while True:
                chunk = fobj.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            fobj.close()

    return _iter_and_close()


def wrap_combined_static_files(wsgi_app, *, static_root: Path, league_slugs: frozenset[str]):
    """Try disk static first; fall through to the combined hub/league app on miss."""
    root = static_root.resolve()
    slugs = frozenset(str(s).strip() for s in league_slugs if str(s).strip())

    def application(environ, start_response):
        rel = static_relative_path(environ.get("PATH_INFO") or "/", slugs)
        if rel is not None:
            file_path = safe_static_file(root, rel)
            if file_path is not None:
                return _wsgi_file_response(file_path, environ, start_response)
        return wsgi_app(environ, start_response)

    return application
