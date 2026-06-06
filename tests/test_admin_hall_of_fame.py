"""Hall of Fame admin form and service behavior."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.services.admin_hall_of_fame import normalize_hof_member_kind


class HallOfFameAdminTest(unittest.TestCase):
    def test_admin_form_can_choose_skater_or_goalie(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_hall_of_fame.html"
        text = path.read_text(encoding="utf-8")

        self.assertIn('name="member_kind"', text)
        self.assertIn('value="skater"', text)
        self.assertIn('value="goalie"', text)
        self.assertIn("edit_row.member_kind", text)

    def test_admin_route_saves_selected_member_kind(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "site_portal.py"
        text = path.read_text(encoding="utf-8")

        self.assertIn('request.form.get("member_kind")', text)
        self.assertIn("member_kind=member_kind", text)

    def test_normalize_hof_member_kind_accepts_only_supported_categories(self) -> None:
        self.assertEqual(normalize_hof_member_kind("Skater"), "skater")
        self.assertEqual(normalize_hof_member_kind(" goalie "), "goalie")
        self.assertIsNone(normalize_hof_member_kind("coach"))


if __name__ == "__main__":
    unittest.main()
