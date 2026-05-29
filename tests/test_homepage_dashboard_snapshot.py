"""Precomputed homepage dashboard snapshots."""
from __future__ import annotations

import unittest

from app import create_app
from app.models import db
from app.services.homepage_dashboard_snapshot import (
    load_ready_homepage_snapshot,
    save_homepage_snapshot,
)
from app.config import make_league_config


class HomepageDashboardSnapshotTests(unittest.TestCase):
    def test_save_and_load_round_trip(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.app_context():
            canonical = type("S", (), {"id": 10})()
            dashboard = type("S", (), {"id": 11})()
            save_homepage_snapshot(
                db.session,
                segment="rs",
                canonical_season=canonical,
                dashboard_season=dashboard,
                body={
                    "segment": "rs",
                    "standings_by_division": [{"name": "East"}],
                    "module_settings": {"visibility": {}},
                },
            )
            loaded = load_ready_homepage_snapshot(
                db.session,
                segment="rs",
                canonical_season=canonical,
                dashboard_season=dashboard,
            )
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.get("segment"), "rs")
            self.assertEqual(len(loaded.get("standings_by_division") or []), 1)
            self.assertNotIn("module_settings", loaded)


if __name__ == "__main__":
    unittest.main()
