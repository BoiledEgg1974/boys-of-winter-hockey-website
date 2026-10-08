"""Admin news BLUP/BLOW tier broadcast helpers."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.news_broadcast import (
    ADMIN_NEWS_SELECT_TIER_LOWER,
    ADMIN_NEWS_SELECT_TIER_UPPER,
    article_broadcast_scope,
    news_franchise_tag_label,
    parse_admin_news_team_selection,
)


class NewsBroadcastParseTests(unittest.TestCase):
    def test_league_selection(self) -> None:
        tid, scope, err = parse_admin_news_team_selection("league", league_slug="bowl-cap")
        self.assertIsNone(tid)
        self.assertEqual(scope, "league")
        self.assertIsNone(err)

    def test_team_id_selection(self) -> None:
        tid, scope, err = parse_admin_news_team_selection("12", league_slug="bowl-fantasy")
        self.assertEqual(tid, 12)
        self.assertIsNone(scope)
        self.assertIsNone(err)

    @patch("app.services.news_broadcast.admin_news_tier_broadcast_enabled", return_value=True)
    def test_blup_selection_when_enabled(self, _enabled) -> None:
        tid, scope, err = parse_admin_news_team_selection(
            ADMIN_NEWS_SELECT_TIER_UPPER, league_slug="bowl-fantasy"
        )
        self.assertIsNone(tid)
        self.assertEqual(scope, "upper")
        self.assertIsNone(err)

    @patch("app.services.news_broadcast.admin_news_tier_broadcast_enabled", return_value=False)
    def test_blup_rejected_when_disabled(self, _enabled) -> None:
        tid, scope, err = parse_admin_news_team_selection(
            ADMIN_NEWS_SELECT_TIER_LOWER, league_slug="bowl-cap"
        )
        self.assertIsNone(tid)
        self.assertIsNone(scope)
        self.assertIn("BLOW", err or "")


class NewsBroadcastLabelTests(unittest.TestCase):
    def test_franchise_tag_league_wide(self) -> None:
        art = SimpleNamespace(team_id=None, broadcast_scope=None)
        self.assertEqual(
            news_franchise_tag_label(art, league_slug="bowl-fantasy"),
            "League",
        )

    def test_franchise_tag_blup(self) -> None:
        art = SimpleNamespace(team_id=None, broadcast_scope="upper")
        self.assertEqual(
            news_franchise_tag_label(art, league_slug="bowl-fantasy"),
            "BLUP League",
        )

    def test_article_broadcast_scope_ignores_team_posts(self) -> None:
        art = SimpleNamespace(team_id=5, broadcast_scope="upper")
        self.assertIsNone(article_broadcast_scope(art))


if __name__ == "__main__":
    unittest.main()
