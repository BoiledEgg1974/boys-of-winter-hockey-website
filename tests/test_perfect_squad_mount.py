from app.config import HOCKEY_LEAGUE_SLUGS
from app.perfect_squad_mount import perfect_squad_home_href, wrap_league_wsgi_with_perfect_squad


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
