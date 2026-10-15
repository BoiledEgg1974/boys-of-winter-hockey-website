import os
import threading
import time

import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response

from app.config import HOCKEY_LEAGUE_SLUGS
from app.perfect_squad_mount import (
    _clear_app_modules,
    _restore_app_modules,
    _snapshot_app_modules,
    perfect_squad_home_href,
    perfect_squad_league_enabled,
    perfect_squad_root,
    wrap_league_wsgi_with_perfect_squad,
)


def test_perfect_squad_home_href_format():
    for slug in HOCKEY_LEAGUE_SLUGS:
        href = perfect_squad_home_href(slug)
        if href is None:
            continue
        assert href == f"/{slug}/perfect-squad/{slug}/"


def test_perfect_squad_disabled_league(monkeypatch):
    monkeypatch.setenv("PERFECT_SQUAD_DISABLED_LEAGUES", "bowl-fantasy")
    assert not perfect_squad_league_enabled("bowl-fantasy")
    assert perfect_squad_league_enabled("bowl-historical")
    assert perfect_squad_home_href("bowl-fantasy") is None
    assert perfect_squad_home_href("bowl-cap") is not None


def test_wrap_league_wsgi_passthrough_for_racing():
    def dummy_app(environ, start_response):
        start_response("200 OK", [])
        return [b"ok"]

    wrapped = wrap_league_wsgi_with_perfect_squad(dummy_app, "bowl-formula")
    assert wrapped is dummy_app


def test_league_and_ps_mounts_do_not_swap_app_modules_concurrently(monkeypatch):
    """Regression: gthread workers must not serve BOWL while PS holds ``sys.modules['app']``."""
    monkeypatch.setattr("app.perfect_squad_mount.perfect_squad_enabled", lambda: True)
    monkeypatch.setattr(
        "app.perfect_squad_mount.perfect_squad_league_enabled",
        lambda slug: slug == "bowl-historical",
    )

    import_errors: list[BaseException] = []

    def fake_ps_wsgi(_environ, start_response):
        saved = _snapshot_app_modules()
        try:
            _clear_app_modules()
            time.sleep(0.25)
            start_response("200 OK", [("Content-Type", "text/plain")])
            return [b"ps"]
        finally:
            _restore_app_modules(saved)

    monkeypatch.setattr(
        "app.perfect_squad_mount._lazy_perfect_squad_wsgi",
        lambda _slug: fake_ps_wsgi,
    )

    def bowl_wsgi(_environ, start_response):
        try:
            from app.config import is_racing_league

            assert is_racing_league("bowl-formula") is True
        except BaseException as exc:  # pragma: no cover - assertion path
            import_errors.append(exc)
            start_response("500 Internal Server Error", [("Content-Type", "text/plain")])
            return [b"fail"]
        start_response("200 OK", [("Content-Type", "text/plain")])
        return [b"bowl"]

    wrapped = wrap_league_wsgi_with_perfect_squad(bowl_wsgi, "bowl-historical")
    client = Client(wrapped, Response)
    bowl_started = threading.Event()
    bowl_done = threading.Event()

    def run_ps():
        client.get("/perfect-squad/bowl-historical/")

    def run_bowl():
        bowl_started.set()
        resp = client.get("/")
        bowl_done.set()
        assert resp.status_code == 200

    ps_thread = threading.Thread(target=run_ps)
    ps_thread.start()
    time.sleep(0.05)
    bowl_thread = threading.Thread(target=run_bowl)
    bowl_thread.start()
    ps_thread.join(timeout=5)
    bowl_thread.join(timeout=5)
    assert not import_errors
    assert bowl_done.is_set()


@pytest.mark.skipif(perfect_squad_root() is None, reason="PERFECT_SQUAD_ROOT / Desktop clone missing")
def test_mounted_perfect_squad_serves_without_app_module_clash(monkeypatch):
    monkeypatch.setenv("DEV_BYPASS_LOGIN", "1")
    monkeypatch.setenv("DEV_BYPASS_USER_ID", "1")
    site_db = perfect_squad_root() / "instance" / "sandbox" / "site_membership.db"
    ps_db = perfect_squad_root() / "instance" / "sandbox" / "perfect-squad.db"
    if site_db.is_file():
        monkeypatch.setenv("SITE_DATABASE_URL", f"sqlite:///{site_db.as_posix()}")
    if ps_db.is_file():
        monkeypatch.setenv("PERFECT_SQUAD_DATABASE_URL", f"sqlite:///{ps_db.as_posix()}")

    def dummy_app(environ, start_response):
        start_response("200 OK", [])
        return [b"bowl"]

    wrapped = wrap_league_wsgi_with_perfect_squad(dummy_app, "bowl-historical")
    client = Client(wrapped, Response)
    resp = client.get("/perfect-squad/bowl-historical/", follow_redirects=False)
    # Perfect Squad mounted (login redirect or home). Do not follow redirects: PS login
    # URLs are league-prefixed and would hit the dummy BOWL app in this harness.
    assert resp.status_code in (200, 302)
    if resp.status_code == 200:
        body = resp.get_data(as_text=True)
        assert "Perfect Squad" in body or "Collection" in body
    else:
        assert "perfect-squad" in (resp.headers.get("Location") or "").lower()
