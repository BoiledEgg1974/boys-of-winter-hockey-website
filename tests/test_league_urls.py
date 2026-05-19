"""League mount URL helpers."""
from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config
from app.league_urls import prefix_league_static_urls


class LeagueUrlsTests(unittest.TestCase):
    def test_prefix_league_static_urls_adds_mount(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        body = {
            "logo_url": "/static/logos/teams/bowl_fantasy/tor-t3.png",
            "nested": [{"player_photo_url": "/static/players/fantasy/x.png"}],
        }
        out = prefix_league_static_urls(body, app=app)
        self.assertEqual(
            out["logo_url"],
            "/bowl-fantasy/static/logos/teams/bowl_fantasy/tor-t3.png",
        )
        self.assertEqual(
            out["nested"][0]["player_photo_url"],
            "/bowl-fantasy/static/players/fantasy/x.png",
        )

    def test_prefix_league_static_urls_idempotent(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        url = "/bowl-fantasy/static/logos/teams/bowl_fantasy/tor-t3.png"
        out = prefix_league_static_urls({"logo_url": url}, app=app)
        self.assertEqual(out["logo_url"], url)

    def test_url_for_static_includes_mount(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_request_context(
            path="/", base_url="http://127.0.0.1/bowl-fantasy/"
        ):
            from flask import url_for

            url = url_for("static", filename="logos/teams/bowl_fantasy/tor-t3.png")
        self.assertEqual(
            url, "/bowl-fantasy/static/logos/teams/bowl_fantasy/tor-t3.png"
        )


if __name__ == "__main__":
    unittest.main()
