"""Discord public URL helpers and embed URL validation."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock

from app.services.discord_events import (
    build_league_public_url,
    build_news_article_public_url,
    enrich_discord_payload_for_bot,
    normalize_discord_payload_url,
    resolve_site_public_base_url,
    sanitize_discord_event_payload,
)
from scripts.league_discord_bot.formatters import _discord_embed_url, sanitize_discord_message_body


class DiscordPublicUrlTest(unittest.TestCase):
    def test_build_url_absolute_when_base_set(self):
        with patch.dict(os.environ, {"SITE_PUBLIC_BASE_URL": "https://www.bowlhockey.com"}, clear=False):
            url = build_league_public_url("bowl-historical", "/draft-hub")
        self.assertEqual(url, "https://www.bowlhockey.com/bowl-historical/draft-hub")

    def test_build_url_empty_when_base_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SITE_PUBLIC_BASE_URL", None)
            url = build_league_public_url("bowl-historical", "/")
        self.assertEqual(url, "")

    def test_discord_embed_url_rejects_relative(self):
        self.assertEqual(_discord_embed_url("/bowl-historical/"), "")
        self.assertEqual(
            _discord_embed_url("https://www.bowlhockey.com/bowl-historical/"),
            "https://www.bowlhockey.com/bowl-historical/",
        )

    def test_resolve_base_from_env(self):
        with patch.dict(os.environ, {"SITE_PUBLIC_BASE_URL": "https://example.com"}, clear=False):
            self.assertEqual(resolve_site_public_base_url(), "https://example.com")

    def test_normalize_relative_historical_url(self):
        with patch.dict(os.environ, {"SITE_PUBLIC_BASE_URL": "https://www.bowlhockey.com"}, clear=False):
            fixed = normalize_discord_payload_url("bowl-historical", "/bowl-historical/")
        self.assertEqual(fixed, "https://www.bowlhockey.com/bowl-historical/")

    def test_news_article_url_uses_headlines_anchor(self):
        with patch.dict(os.environ, {"SITE_PUBLIC_BASE_URL": "https://www.bowlhockey.com"}, clear=False):
            url = build_news_article_public_url("bowl-historical", 42)
        self.assertEqual(url, "https://www.bowlhockey.com/bowl-historical/league-headlines#a42")

    def test_sanitize_payload_upgrades_home_url_with_article_id(self):
        with patch.dict(os.environ, {"SITE_PUBLIC_BASE_URL": "https://www.bowlhockey.com"}, clear=False):
            out = sanitize_discord_event_payload(
                "bowl-historical",
                {"article_id": 9, "url": "/", "title": "Test"},
            )
        self.assertEqual(
            out["url"],
            "https://www.bowlhockey.com/bowl-historical/league-headlines#a9",
        )

    def test_sanitize_payload_drops_relative_without_base(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SITE_PUBLIC_BASE_URL", None)
            out = sanitize_discord_event_payload("bowl-historical", {"url": "/bowl-historical/"})
        self.assertNotIn("url", out)

    def test_sanitize_message_body_strips_bad_embed_url(self):
        body = sanitize_discord_message_body(
            {
                "content": "hi",
                "embeds": [{"title": "T", "url": "/bowl-historical/"}],
            }
        )
        self.assertEqual(body.get("embeds"), [{"title": "T"}])

    def test_news_payload_enrichment_adds_team_gm_mention(self):
        session = MagicMock()
        session.get.return_value = SimpleNamespace(
            id=42,
            league_slug="bowl-cap",
            team_id=7,
            title="Team story",
            body="Full story.",
            image_rel_path=None,
        )
        with patch(
            "app.services.discord_events._discord_user_mention_for_team",
            return_value="<@123456789012345678>",
        ):
            out = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="gm_news_published",
                payload={"article_id": 42},
            )

        self.assertEqual(out["team_id"], 7)
        self.assertEqual(out["team_gm_mention"], "<@123456789012345678>")


if __name__ == "__main__":
    unittest.main()
