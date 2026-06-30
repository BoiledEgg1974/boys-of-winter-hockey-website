"""Draft Eligible mock draft tab smoke tests."""
from __future__ import annotations

import unittest

from app import create_app
from app.config import make_league_config
from app.services.draft_mock import (
    _goalie_profile_from_ovrs,
    _should_take_goalie_now,
    forces_goalie_need,
)


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

    def test_forces_goalie_need_respects_crease_quality(self) -> None:
        self.assertTrue(forces_goalie_need(None))
        self.assertTrue(forces_goalie_need(_goalie_profile_from_ovrs([])))
        self.assertTrue(forces_goalie_need(_goalie_profile_from_ovrs([45])))
        self.assertFalse(forces_goalie_need(_goalie_profile_from_ovrs([62])))
        self.assertFalse(forces_goalie_need(_goalie_profile_from_ovrs([58, 50])))
        self.assertFalse(forces_goalie_need(_goalie_profile_from_ovrs([55, 47])))
        self.assertTrue(forces_goalie_need(_goalie_profile_from_ovrs([58, 40])))
        self.assertTrue(forces_goalie_need(_goalie_profile_from_ovrs([47, 46])))

    def test_should_take_goalie_now_avoids_early_reaches(self) -> None:
        from types import SimpleNamespace

        class P(SimpleNamespace):
            pass

        pool = [
            P(id=1, position="G", full_name="Goalie A"),
            P(id=2, position="LW", full_name="Skater A"),
        ]
        ranks = {1: 21, 2: 2}
        self.assertFalse(_should_take_goalie_now(pool, ranks, round_no=1))
        ranks_close = {1: 3, 2: 2}
        self.assertTrue(_should_take_goalie_now(pool, ranks_close, round_no=1))
        ranks_gap = {1: 10, 2: 2}
        self.assertFalse(_should_take_goalie_now(pool, ranks_gap, round_no=1))


if __name__ == "__main__":
    unittest.main()
