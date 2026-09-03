"""League factory must finish blueprint setup before the first register_blueprint."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.config import Config
from app.league_db import db


def _isolated_config(slug: str, root: Path):
    class _TestConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{(root / f'{slug}.db').as_posix()}"
        SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{(root / 'site.db').as_posix()}"
        TESTING = True
        WTF_CSRF_ENABLED = False
        LEAGUE_SLUG = slug
        RAW_IMPORT_DIR = root
        SQLALCHEMY_BINDS = {}

    return _TestConfig


class CreateAppBlueprintSetupTests(unittest.TestCase):
    def test_hockey_app_after_racing_app_keeps_ap_and_bowl_six_routes(self) -> None:
        """Reproduce production WSGI order: Formula (or Demo) init, then a hockey AP hit."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            racing = create_app(_isolated_config("bowl-formula", root))
            hockey = create_app(_isolated_config("bowl-cap", root))
            try:
                racing_endpoints = {rule.endpoint for rule in racing.url_map.iter_rules()}
                hockey_endpoints = {rule.endpoint for rule in hockey.url_map.iter_rules()}
                self.assertIn("site_admin.admin_bowl_six_score", racing_endpoints)
                self.assertIn("site_gm.action_points_page", hockey_endpoints)
                self.assertIn("site_admin.admin_bowl_six_score", hockey_endpoints)

                with hockey.test_client() as client:
                    page = client.get("/action-points", follow_redirects=False)
                    self.assertIn(page.status_code, (200, 302), page.get_data(as_text=True))
            finally:
                with hockey.app_context():
                    db.session.remove()
                    for engine in db.engines.values():
                        engine.dispose()
