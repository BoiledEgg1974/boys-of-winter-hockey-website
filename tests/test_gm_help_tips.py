"""GM Help/Tips page template and route marker tests."""
from __future__ import annotations

import unittest
from pathlib import Path


class GmHelpTipsTest(unittest.TestCase):
    def test_gm_help_tips_template_markers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "gm_help_tips.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        portal = (root / "app" / "routes" / "site_portal.py").read_text(encoding="utf-8")
        base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        admin = (root / "app" / "templates" / "admin_site_home.html").read_text(encoding="utf-8")

        for marker in (
            "Franchise Hockey Manager Help",
            "gm-help-tips__quickstart",
            "Multiplayer Checklist",
            "Export to the league server",
            'id="help-roster"',
            'id="help-lineups"',
            'id="help-schedule"',
            'id="help-lines"',
            'id="help-tactics"',
            'id="help-training"',
            'id="help-chemistry"',
            'id="help-finances"',
            "player_status_injured.png",
            "player_status_lightly_injured.png",
            "status_vac.png",
            "status_int.png",
            "gm-help-tips__status--unf",
            "Skill groups",
            "Team harmony",
            "Annual finances",
        ):
            self.assertIn(marker, template)

        self.assertIn("def gm_help_tips_page", portal)
        self.assertIn('"/help-tips"', portal)
        self.assertIn("gm_help_tips.html", portal)
        self.assertIn("Help/Tips is available to active GMs", portal)
        self.assertIn("site_gm.gm_help_tips_page", base)
        self.assertIn("Help/Tips", base)
        self.assertIn("site_gm.gm_help_tips_page", admin)
        self.assertIn("FHM Help/Tips", admin)
        self.assertIn(".gm-help-tips__layout", css)
        self.assertIn(".gm-help-tips__toc", css)
        self.assertIn(".gm-help-tips__callout", css)


if __name__ == "__main__":
    unittest.main()
