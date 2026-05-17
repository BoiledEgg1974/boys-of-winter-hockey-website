"""Discord public URL helpers and embed URL validation."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.discord_events import build_league_public_url, resolve_site_public_base_url
from scripts.league_discord_bot.formatters import _discord_embed_url


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


if __name__ == "__main__":
    unittest.main()
