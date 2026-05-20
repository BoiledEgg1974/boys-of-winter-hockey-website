"""Site-wide session cookie paths (hub + league mounts share one login)."""
from __future__ import annotations

import unittest

from app.auth_login import clear_legacy_mount_session_cookies, login_manager
from app.config import Config, make_league_config


class SiteSessionCookieTest(unittest.TestCase):
    def test_league_config_overrides_application_root_cookie_paths(self):
        LeagueConfig = make_league_config("bowl-historical")
        self.assertEqual(LeagueConfig.APPLICATION_ROOT, "/bowl-historical")
        self.assertEqual(LeagueConfig.SESSION_COOKIE_PATH, "/")
        self.assertEqual(LeagueConfig.REMEMBER_COOKIE_PATH, "/")

    def test_base_config_cookie_paths(self):
        self.assertEqual(Config.SESSION_COOKIE_PATH, "/")
        self.assertEqual(Config.REMEMBER_COOKIE_PATH, "/")

    def test_shared_login_manager_singleton(self):
        from app import login_manager as league_login_manager
        from hub import login_manager as hub_login_manager

        self.assertIs(league_login_manager, hub_login_manager)
        self.assertIs(league_login_manager, login_manager)

    def test_clear_legacy_mount_cookies_sets_expired_path_scoped_cookies(self):
        from flask import Flask

        app = Flask(__name__)
        with app.test_request_context():
            resp = clear_legacy_mount_session_cookies(app.response_class())
        joined = "; ".join(resp.headers.getlist("Set-Cookie")).lower()
        for slug in ("bowl-historical", "bowl-cap", "bowl-fantasy"):
            self.assertIn(f"path=/{slug}", joined)
        self.assertIn("session=", joined)
        self.assertIn("remember_token=", joined)


if __name__ == "__main__":
    unittest.main()
