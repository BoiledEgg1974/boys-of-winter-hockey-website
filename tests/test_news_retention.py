"""Published news 45-day retention purge and age filter."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services.news_retention import (
    DEFAULT_PUBLISHED_NEWS_RETENTION_DAYS,
    published_news_age_filter,
    published_news_cutoff_utc,
    purge_old_published_news,
)


class NewsRetentionTest(unittest.TestCase):
    def test_default_retention_is_45_days(self) -> None:
        self.assertEqual(DEFAULT_PUBLISHED_NEWS_RETENTION_DAYS, 45)
        cutoff = published_news_cutoff_utc()
        age = datetime.utcnow() - cutoff
        self.assertGreaterEqual(age.days, 44)
        self.assertLessEqual(age.days, 46)

    def test_purge_deletes_stale_published_articles(self) -> None:
        stale = MagicMock()
        stale.id = 101
        stale.image_rel_path = None
        fresh = MagicMock()
        fresh.id = 102
        session = MagicMock()
        session.scalars.return_value.all.return_value = [stale]
        votes_result = MagicMock(rowcount=2)
        comments_result = MagicMock(rowcount=3)
        articles_result = MagicMock(rowcount=1)
        session.execute.side_effect = [votes_result, comments_result, articles_result]

        with patch(
            "app.services.news_retention.published_news_cutoff_utc",
            return_value=datetime(2020, 1, 1),
        ):
            out = purge_old_published_news(session, league_slug="bowl-cap", days=45)

        self.assertEqual(out["articles"], 1)
        self.assertEqual(out["comments"], 3)
        self.assertEqual(out["votes"], 2)
        session.commit.assert_called_once()

    def test_purge_noop_when_nothing_stale(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = []
        out = purge_old_published_news(session, league_slug="bowl-fantasy")
        self.assertEqual(out["articles"], 0)
        session.execute.assert_not_called()
        session.commit.assert_not_called()

    def test_age_filter_expression_compiles(self) -> None:
        from app.site_models import NewsArticle

        expr = published_news_age_filter(NewsArticle, days=45)
        self.assertIsNotNone(expr)


class NewsRetentionRoutesTest(unittest.TestCase):
    def test_league_headlines_renders_all_leagues(self) -> None:
        for slug in ("bowl-historical", "bowl-fantasy", "bowl-cap"):
            app = create_app(make_league_config(slug))
            with app.test_client() as client:
                r = client.get("/league-headlines")
                self.assertEqual(r.status_code, 200, slug)


if __name__ == "__main__":
    unittest.main()
