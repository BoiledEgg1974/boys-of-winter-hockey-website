"""WSGI static bypass for league-mounted assets."""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from app.static_wsgi import (
    safe_static_file,
    static_relative_path,
    wrap_combined_static_files,
)


def test_static_relative_path_hub_and_league():
    slugs = frozenset({"bowl-cap", "bowl-fantasy"})
    assert static_relative_path("/static/logos/foo.png", slugs) == "logos/foo.png"
    assert (
        static_relative_path("/bowl-cap/static/players/a.png", slugs) == "players/a.png"
    )
    assert static_relative_path("/bowl-unknown/static/x.png", slugs) is None
    assert static_relative_path("/bowl-cap/team/foo", slugs) is None


def test_safe_static_file_rejects_traversal(tmp_path: Path):
    root = tmp_path / "static"
    root.mkdir()
    (root / "ok.txt").write_text("hi", encoding="utf-8")
    assert safe_static_file(root, "ok.txt") == (root / "ok.txt").resolve()
    assert safe_static_file(root, "../secret") is None
    assert safe_static_file(root, "missing.txt") is None


def test_wrap_combined_static_files_serves_without_inner_app(tmp_path: Path):
    root = tmp_path / "static"
    players = root / "players"
    players.mkdir(parents=True)
    (players / "test.png").write_bytes(b"\x89PNG")

    def inner(environ, start_response):
        raise AssertionError("league app must not load for static hit")

    app = wrap_combined_static_files(
        inner,
        static_root=root,
        league_slugs=frozenset({"bowl-cap"}),
    )
    status: list[str] = []
    headers: list[tuple[str, str]] = []

    def start(s, h):
        status.append(s)
        headers.extend(h)

    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": "/bowl-cap/static/players/test.png",
        "wsgi.input": BytesIO(b""),
        "wsgi.errors": open("NUL" if Path("NUL").exists() else "/dev/null", "w"),
    }
    body = b"".join(app(environ, start))
    assert status == ["200 OK"]
    assert body == b"\x89PNG"
