import os

import pytest
from werkzeug.test import Client
from werkzeug.wrappers import Response

from app.config import HOCKEY_LEAGUE_SLUGS
from app.perfect_squad_mount import (
    perfect_squad_home_href,
    perfect_squad_root,
    wrap_league_wsgi_with_perfect_squad,
)


def test_perfect_squad_home_href_format():
    for slug in HOCKEY_LEAGUE_SLUGS:
        href = perfect_squad_home_href(slug)
        if href is None:
            continue
        assert href == f"/{slug}/perfect-squad/{slug}/"


def test_wrap_league_wsgi_passthrough_for_racing():
    def dummy_app(environ, start_response):
        start_response("200 OK", [])
        return [b"ok"]

    wrapped = wrap_league_wsgi_with_perfect_squad(dummy_app, "bowl-formula")
    assert wrapped is dummy_app


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
    resp = client.get("/perfect-squad/bowl-historical/", follow_redirects=True)
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Perfect Squad" in body or "Collection" in body
