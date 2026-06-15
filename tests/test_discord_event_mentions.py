"""Discord outbound mention resolution."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.site_models import NewsArticle
from app.services.discord_events import enrich_discord_payload_for_bot, _team_gm_mention_for_payload


class DiscordEventMentionTests(unittest.TestCase):
    def test_news_payload_prefers_internal_team_id_over_fhm_team_id(self) -> None:
        session = MagicMock()

        with (
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@111111111111111111>",
            ) as by_team,
            patch(
                "app.services.discord_events._discord_user_mention_for_fhm_team",
                return_value="<@222222222222222222>",
            ) as by_fhm,
        ):
            mention = _team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={"team_id": 28, "fhm_team_id": 224},
            )

        self.assertEqual(mention, "<@111111111111111111>")
        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=28)
        by_fhm.assert_not_called()

    def test_news_payload_falls_back_to_fhm_team_id_without_team_match(self) -> None:
        session = MagicMock()

        with (
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="",
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_fhm_team",
                return_value="<@222222222222222222>",
            ) as by_fhm,
        ):
            mention = _team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={"team_id": 28, "fhm_team_id": 224},
            )

        self.assertEqual(mention, "<@222222222222222222>")
        by_fhm.assert_called_once_with(
            session, league_slug="bowl-cap", fhm_team_id="224"
        )

    def test_gm_news_delivery_recomputes_stale_queued_mentions(self) -> None:
        session = MagicMock()
        article = SimpleNamespace(
            id=608,
            league_slug="bowl-cap",
            title="Derek King Has Competed in 1000 Games",
            body="Detroit story.",
            body_preview="Detroit story.",
            image_rel_path="",
            team_id=9,
        )
        session.get.return_value = article

        with patch(
            "app.services.discord_events._discord_user_mention_for_team",
            return_value="<@111111111111111111>",
        ) as by_team:
            payload = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="gm_news_published",
                payload={
                    "article_id": 608,
                    "team_id": 9,
                    "fhm_team_id": 9,
                    "team_gm_mention": "<@222222222222222222>",
                    "gm_mentions": "<@222222222222222222>",
                },
            )

        session.get.assert_called_with(NewsArticle, 608)
        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=9)
        self.assertEqual(payload["team_gm_mention"], "<@111111111111111111>")
        self.assertNotIn("gm_mentions", payload)


if __name__ == "__main__":
    unittest.main()
