"""Game records admin page visibility and template."""
from __future__ import annotations

import unittest
from pathlib import Path


class GameRecordsAdminTest(unittest.TestCase):
    def test_admin_home_links_game_records(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_site_home.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("admin_game_records", text)
        self.assertIn("Game records", text)

    def test_admin_template_has_baseline_form(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_game_records.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("game-records-admin-form", text)
        self.assertIn('name="metric_key"', text)
        self.assertIn('action" value="delete"', text)

    def test_public_nav_links_game_records(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("game_records_page", text)


if __name__ == "__main__":
    unittest.main()
