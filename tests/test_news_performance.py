"""Headlines / homepage news performance helpers."""
from __future__ import annotations

import unittest

from app.services.news_engagement import engagement_bundle_for_articles
from app.services.news_text import news_body_excerpt


class NewsBodyExcerptTests(unittest.TestCase):
    def test_short_body_unchanged(self) -> None:
        self.assertEqual(news_body_excerpt("Hello league"), "Hello league")

    def test_long_body_truncates_with_ellipsis(self) -> None:
        body = "word " * 200
        out = news_body_excerpt(body, max_len=50)
        self.assertTrue(out.endswith("…"))
        self.assertLess(len(out), len(body))

    def test_gm_article_body_limit_is_1500(self) -> None:
        from app.services.news_text import GM_ARTICLE_BODY_MAX_LEN
        from pathlib import Path

        self.assertEqual(GM_ARTICLE_BODY_MAX_LEN, 1500)
        gm_html = (Path(__file__).resolve().parents[1] / "app" / "templates" / "league_news_gm.html").read_text(
            encoding="utf-8"
        )
        admin_html = (
            Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_news_compose.html"
        ).read_text(encoding="utf-8")
        self.assertIn("gm_article_body_max_len", gm_html)
        self.assertIn('maxlength="{{ gm_article_body_max_len }}"', gm_html)
        self.assertIn('name="body"', admin_html)
        self.assertNotIn("maxlength", admin_html.split('name="body"', 1)[1].split("</textarea>", 1)[0])


class EngagementBundleCommentsTests(unittest.TestCase):
    def test_zero_comments_skips_comment_query(self) -> None:
        class _Rows:
            def all(self):
                return []

        class _Session:
            def execute(self, *_a, **_k):
                return _Rows()

            def scalars(self, *_a, **_k):
                return _Rows()

        out = engagement_bundle_for_articles(
            _Session(), "bowl-historical", [1, 2], None, comments_per_article=0
        )
        self.assertEqual(out[1]["comments"], [])
        self.assertEqual(out[2]["comments"], [])


if __name__ == "__main__":
    unittest.main()
