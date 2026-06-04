"""Draft Eligible mock draft tab smoke tests."""
from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config


class DraftEligibleMockDraftTest(unittest.TestCase):
    def test_mock_draft_tab_renders_for_all_leagues(self) -> None:
        for slug in ("bowl-cap", "bowl-historical", "bowl-fantasy"):
            with self.subTest(slug=slug):
                app = create_app(make_league_config(slug))
                r = app.test_client().get("/draft-eligible?tab=mock")
                self.assertEqual(r.status_code, 200)
                body = r.get_data(as_text=True)
                self.assertIn("Mock Draft", body)
                self.assertIn("Team Picking", body)
                self.assertIn("Original Pick", body)


if __name__ == "__main__":
    unittest.main()
