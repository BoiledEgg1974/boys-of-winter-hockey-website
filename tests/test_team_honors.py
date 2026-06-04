"""Team honors admin and team page display."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.services.team_honors import team_honors_page_bundle
from app.services.team_honors_media import retired_jersey_filename, victory_banner_filename


class TeamHonorsTemplateTest(unittest.TestCase):
    def test_admin_home_links_team_honors(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "templates" / "admin_site_home.html").read_text(encoding="utf-8")
        self.assertIn("admin_team_honors", text)
        self.assertIn("Team honors", text)

    def test_admin_template_has_upload_forms(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "templates" / "admin_team_honors.html").read_text(encoding="utf-8")
        self.assertIn('action" value="save_retired"', text)
        self.assertIn('action" value="save_banner"', text)
        self.assertIn("retired_section_enabled", text)
        self.assertIn('name="jersey_image"', text)
        self.assertIn('name="banner_image"', text)

    def test_team_page_has_honors_section(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "templates" / "team.html").read_text(encoding="utf-8")
        self.assertIn("team-honors", text)
        self.assertIn("Retired Number", text)
        self.assertIn("Championship Banners", text)
        self.assertIn("team-honors__jersey-num", text)


class TeamHonorsMediaTest(unittest.TestCase):
    def test_filename_conventions(self) -> None:
        self.assertEqual(retired_jersey_filename(12, 99), "T12-Jersey99")
        self.assertEqual(victory_banner_filename(4, 2), "T4-Banner2")


class TeamHonorsBundleTest(unittest.TestCase):
    def test_empty_bundle_hides_section(self) -> None:
        class _Session:
            def get(self, _model, _tid):
                return None

            def scalars(self, _q):
                class _R:
                    def all(self):
                        return []

                return _R()

        bundle = team_honors_page_bundle(_Session(), 1)
        self.assertFalse(bundle["team_honors_show_section"])
        self.assertFalse(bundle["team_honors_show_retired_panel"])
        self.assertFalse(bundle["team_honors_show_banner_panel"])


if __name__ == "__main__":
    unittest.main()
