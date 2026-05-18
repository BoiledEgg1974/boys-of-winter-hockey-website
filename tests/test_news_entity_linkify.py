"""News article entity linkify."""
from __future__ import annotations

import unittest

from app.services.news_entity_linkify import _LinkPhrase, linkify_plain_text


class NewsEntityLinkifyTest(unittest.TestCase):
    def test_links_team_name_with_word_boundaries(self):
        phrases = (
            _LinkPhrase("Toronto Maple Leafs", "/teams/toronto-maple-leafs", 30),
            _LinkPhrase("Bruins", "/teams/boston-bruins", 10),
        )
        html = linkify_plain_text(
            "The Toronto Maple Leafs beat the Bruins in overtime.",
            phrases,
        )
        self.assertIn('href="/teams/toronto-maple-leafs"', html)
        self.assertIn("Toronto Maple Leafs</a>", html)
        self.assertIn('href="/teams/boston-bruins"', html)

    def test_does_not_link_inside_longer_word(self):
        phrases = (_LinkPhrase("Leafs", "/teams/leafs", 5),)
        html = linkify_plain_text("Mapleleafs are not linked.", phrases)
        self.assertNotIn("<a ", html)

    def test_prefers_longer_phrase(self):
        phrases = (
            _LinkPhrase("Connor Bedard", "/players/1", 25),
            _LinkPhrase("Bedard", "/players/2", 5),
        )
        html = linkify_plain_text("Connor Bedard scored.", phrases)
        self.assertIn('href="/players/1"', html)
        self.assertNotIn('href="/players/2"', html)


if __name__ == "__main__":
    unittest.main()
