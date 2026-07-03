"""Discord public URL helpers and embed URL validation."""
from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import MagicMock

from app.services.discord_events import (
    _ensure_team_gm_mention_for_payload,
    _team_gm_mention_for_payload,
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
        detroit = SimpleNamespace(
            id=7,
            fhm_team_id="9",
            abbreviation="DET",
            name="Detroit",
            slug="detroit-red-wings",
        )
        session.get.return_value = SimpleNamespace(
            id=42,
            league_slug="bowl-cap",
            team_id=7,
            title="Team story",
            body="Full story.",
            image_rel_path=None,
        )
        with (
            patch(
                "app.services.discord_events._resolve_league_team_for_news",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events.team_fields_for_discord",
                return_value={"team_id": 7, "fhm_team_id": 9, "team_abbrev": "DET"},
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@123456789012345678>",
            ),
        ):
            out = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="gm_news_published",
                payload={"article_id": 42},
            )

        self.assertEqual(out["team_id"], 7)
        self.assertEqual(out["team_gm_mention"], "<@123456789012345678>")

    def test_staff_payload_enrichment_adds_team_gm_mention(self):
        session = MagicMock()
        req = SimpleNamespace(
            id=12,
            league_slug="bowl-cap",
            team_id=7,
            user_id=44,
            request_type="hire",
            staff_name="Coach Example",
            role="scout",
        )
        team = SimpleNamespace(id=7, fhm_team_id="3", abbreviation="TOR")
        session.get.side_effect = lambda model, row_id: req if row_id == 12 else team

        with patch(
            "app.services.discord_events._discord_user_mention_for_team",
            return_value="<@123456789012345678>",
        ):
            out = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="staff_transaction_posted",
                payload={
                    "request_id": 12,
                    "team_id": 28,
                    "fhm_team_id": 227,
                    "team_abbrev": "ATL",
                    "role_label": "Scout",
                },
            )

        self.assertEqual(out["team_id"], 7)
        self.assertEqual(out["team_abbrev"], "TOR")
        self.assertEqual(out["team_gm_mention"], "<@123456789012345678>")

    def test_generic_payload_enrichment_adds_team_gm_mention(self):
        session = MagicMock()
        team = SimpleNamespace(id=7, fhm_team_id="9", abbreviation="TOR")

        with (
            patch(
                "app.services.discord_events._resolve_team_for_news_discord",
                return_value=team,
            ),
            patch(
                "app.services.discord_events.team_fields_for_discord",
                return_value={"team_id": 7, "fhm_team_id": 9, "team_abbrev": "TOR"},
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@123456789012345678>",
            ),
        ):
            out = _ensure_team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={"team_id": 7, "title": "Trade update"},
            )

        self.assertEqual(out["team_gm_mention"], "<@123456789012345678>")

    def test_generic_payload_enrichment_does_not_duplicate_draft_mentions(self):
        session = MagicMock()

        out = _ensure_team_gm_mention_for_payload(
            session,
            league_slug="bowl-fantasy",
            payload={"team_id": 7, "gm_mentions": "<@222222222222222222>"},
        )

        self.assertNotIn("team_gm_mention", out)
        session.scalar.assert_not_called()

    def test_generic_payload_enrichment_refreshes_stale_team_gm_mention(self):
        session = MagicMock()
        detroit = SimpleNamespace(id=5, fhm_team_id="9", abbreviation="DET")

        with (
            patch(
                "app.services.discord_events._resolve_team_for_news_discord",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events.team_fields_for_discord",
                return_value={"team_id": 5, "fhm_team_id": 9, "team_abbrev": "DET"},
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@111111111111111111>",
            ),
        ):
            out = _ensure_team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={
                    "team_id": 28,
                    "fhm_team_id": 9,
                    "team_abbrev": "DET",
                    "team_gm_mention": "<@222222222222222222>",
                },
            )

        self.assertEqual(out["team_id"], 5)
        self.assertEqual(out["team_gm_mention"], "<@111111111111111111>")

    def test_team_gm_mention_prefers_fhm_team_id_over_mismatched_team_id(self):
        session = MagicMock()
        atlanta = SimpleNamespace(id=28, fhm_team_id="227", abbreviation="ATL")
        detroit = SimpleNamespace(id=5, fhm_team_id="9", abbreviation="DET")

        with (
            patch(
                "app.services.discord_events._resolve_league_team_for_news",
                return_value=atlanta,
            ),
            patch(
                "app.services.discord_events._league_team_by_fhm",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_fhm_team",
                return_value="<@111111111111111111>",
            ) as by_fhm,
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@222222222222222222>",
            ) as by_team,
        ):
            mention = _team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={"team_id": 28, "fhm_team_id": 9, "team_abbrev": "DET"},
            )

        self.assertEqual(mention, "<@111111111111111111>")
        by_fhm.assert_called_once_with(session, league_slug="bowl-cap", fhm_team_id="9")
        by_team.assert_not_called()

    def test_team_gm_mention_uses_fhm_when_team_id_points_at_wrong_franchise(self):
        session = MagicMock()
        atlanta = SimpleNamespace(id=28, fhm_team_id="227", abbreviation="ATL")
        detroit = SimpleNamespace(
            id=5,
            fhm_team_id="9",
            abbreviation="DET",
            name="Detroit",
            nickname="Red Wings",
            full_display_name=lambda: "Detroit Red Wings",
        )

        with (
            patch(
                "app.services.discord_events._resolve_league_team_for_news",
                return_value=atlanta,
            ),
            patch(
                "app.services.discord_events._league_team_by_fhm",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@111111111111111111>",
            ) as by_team,
        ):
            mention = _team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={
                    "team_id": 28,
                    "fhm_team_id": 9,
                    "team_name": "Detroit Red Wings",
                },
            )

        self.assertEqual(mention, "<@111111111111111111>")
        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=5)

    def test_team_gm_mention_falls_back_to_team_pk_when_fhm_lookup_empty(self):
        session = MagicMock()
        atlanta = SimpleNamespace(id=28, fhm_team_id="227", abbreviation="ATL")
        detroit = SimpleNamespace(id=5, fhm_team_id="9", abbreviation="DET")

        with (
            patch(
                "app.services.discord_events._resolve_league_team_for_news",
                return_value=atlanta,
            ),
            patch(
                "app.services.discord_events._league_team_by_fhm",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_fhm_team",
                return_value="",
            ) as by_fhm,
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="",
            ) as by_team,
        ):
            mention = _team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={"team_id": 28, "fhm_team_id": 9, "team_abbrev": "DET"},
            )

        self.assertEqual(mention, "")
        by_fhm.assert_called_once_with(session, league_slug="bowl-cap", fhm_team_id="9")
        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=5)

    def test_news_payload_enrichment_uses_article_team_for_gm_mention(self):
        session = MagicMock()
        detroit = SimpleNamespace(
            id=5,
            fhm_team_id="9",
            abbreviation="DET",
            name="Detroit",
            slug="detroit-red-wings",
        )
        session.get.return_value = SimpleNamespace(
            id=42,
            league_slug="bowl-cap",
            team_id=5,
            title="Big Game for Peter Roed",
            body="Full story.",
            image_rel_path=None,
        )
        with (
            patch(
                "app.services.discord_events._resolve_league_team_for_news",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events.team_fields_for_discord",
                return_value={"team_id": 5, "fhm_team_id": 9, "team_abbrev": "DET"},
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_fhm_team",
                return_value="<@111111111111111111>",
            ) as by_fhm,
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@222222222222222222>",
            ),
        ):
            out = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="admin_news_published",
                payload={"article_id": 42, "team_id": 28, "fhm_team_id": 227, "team_abbrev": "ATL"},
            )

        self.assertEqual(out["team_id"], 5)
        self.assertEqual(out["fhm_team_id"], 9)
        self.assertEqual(out["team_gm_mention"], "<@111111111111111111>")
        by_fhm.assert_called_once_with(session, league_slug="bowl-cap", fhm_team_id="9")


if __name__ == "__main__":
    unittest.main()
