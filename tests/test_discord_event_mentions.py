"""Discord outbound mention resolution."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.site_models import NewsArticle
from app.services.discord_events import (
    enrich_discord_payload_for_bot,
    _resolve_league_team_for_news,
    _resolve_team_for_discord_payload,
    _resolve_team_for_news_discord,
    _team_gm_mention_for_payload,
    resolve_news_article_team,
)


class DiscordEventMentionTests(unittest.TestCase):
    def test_resolve_team_for_discord_payload_prefers_fhm_when_team_id_is_stale(self) -> None:
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
        ):
            team = _resolve_team_for_discord_payload(
                session,
                {
                    "team_id": 28,
                    "fhm_team_id": 9,
                    "team_name": "Detroit Red Wings",
                },
            )

        self.assertIs(team, detroit)

    def test_news_payload_mention_uses_fhm_franchise_id_from_resolved_team(self) -> None:
        session = MagicMock()
        detroit = SimpleNamespace(id=5, fhm_team_id="9", abbreviation="DET")

        with (
            patch(
                "app.services.discord_events._resolve_team_for_discord_payload",
                return_value=detroit,
            ),
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
                payload={"team_id": 5, "fhm_team_id": 227, "team_abbrev": "DET"},
            )

        self.assertEqual(mention, "<@222222222222222222>")
        by_fhm.assert_called_once_with(session, league_slug="bowl-cap", fhm_team_id="9")
        by_team.assert_not_called()

    def test_news_payload_prefers_fhm_match_when_internal_id_collides(self) -> None:
        session = MagicMock()
        detroit = SimpleNamespace(id=5, fhm_team_id="9", abbreviation="DET")

        with (
            patch(
                "app.services.discord_events._resolve_team_for_discord_payload",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_fhm_team",
                return_value="<@111111111111111111>",
            ) as by_fhm,
        ):
            mention = _team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={"team_id": 9, "fhm_team_id": 9, "team_abbrev": "DET"},
            )

        self.assertEqual(mention, "<@111111111111111111>")
        by_fhm.assert_called_once_with(session, league_slug="bowl-cap", fhm_team_id="9")

    def test_news_payload_falls_back_to_fhm_team_id_without_team_match(self) -> None:
        session = MagicMock()

        with (
            patch(
                "app.services.discord_events._resolve_team_for_discord_payload",
                return_value=None,
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

        self.assertEqual(mention, "")
        by_fhm.assert_not_called()

    def test_gm_news_delivery_recomputes_stale_queued_mentions(self) -> None:
        session = MagicMock()
        article = SimpleNamespace(
            id=608,
            league_slug="bowl-cap",
            title="Derek King Has Competed in 1000 Games",
            body="Detroit story.",
            body_preview="Detroit story.",
            image_rel_path="",
            team_id=5,
        )
        detroit = SimpleNamespace(
            id=5,
            fhm_team_id="9",
            full_display_name=lambda: "Detroit Red Wings",
            abbreviation="DET",
            slug="detroit-red-wings",
        )
        session.get.return_value = article

        with (
            patch(
                "app.services.discord_events._resolve_league_team_for_news",
                return_value=detroit,
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@111111111111111111>",
            ) as by_team,
            patch(
                "app.services.discord_events.team_fields_for_discord",
                return_value={"team_id": 5, "fhm_team_id": 9, "team_abbrev": "DET"},
            ),
        ):
            payload = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="gm_news_published",
                payload={
                    "article_id": 608,
                    "team_id": 28,
                    "fhm_team_id": 227,
                    "team_gm_mention": "<@222222222222222222>",
                    "gm_mentions": "<@222222222222222222>",
                },
            )

        session.get.assert_called_with(NewsArticle, 608)
        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=5)
        self.assertEqual(payload["team_gm_mention"], "<@111111111111111111>")
        self.assertEqual(payload["team_id"], 5)
        self.assertEqual(payload["fhm_team_id"], 9)
        self.assertNotIn("gm_mentions", payload)

    def test_resolve_league_team_prefers_pk_when_fhm_franchise_collides(self) -> None:
        from app.models import Team

        session = MagicMock()
        buffalo = SimpleNamespace(id=12, fhm_team_id="17", abbreviation="BUF")
        dallas = SimpleNamespace(id=8, fhm_team_id="12", abbreviation="DAL")

        def _get(model, pk):
            if model is Team and pk == 12:
                return buffalo
            return None

        session.get.side_effect = _get
        session.scalar.return_value = dallas

        team = _resolve_league_team_for_news(session, 12)

        self.assertIs(team, buffalo)

    def test_resolve_news_article_team_prefers_article_team_id(self) -> None:
        from app.models import Team

        session = MagicMock()
        detroit = SimpleNamespace(id=5, fhm_team_id="9", abbreviation="DET")
        atlanta = SimpleNamespace(id=28, fhm_team_id="227", abbreviation="ATL")
        article = SimpleNamespace(
            league_slug="bowl-cap",
            team_id=5,
            author_user_id=42,
        )

        def _get(model, pk):
            if model is Team and pk == 5:
                return detroit
            if model is Team and pk == 28:
                return atlanta
            return None

        session.get.side_effect = _get

        with patch(
            "app.services.discord_events._resolve_league_team_for_news",
            return_value=detroit,
        ) as by_team_id:
            team = resolve_news_article_team(session, article)

        self.assertIs(team, detroit)
        by_team_id.assert_called_once_with(session, 5)
        session.scalar.assert_not_called()

    def test_resolve_news_article_team_falls_back_to_author_gm_membership(self) -> None:
        from app.models import Team

        session = MagicMock()
        buffalo = SimpleNamespace(id=12, fhm_team_id="17", abbreviation="BUF")
        article = SimpleNamespace(
            league_slug="bowl-cap",
            team_id=None,
            author_user_id=42,
        )
        mem = SimpleNamespace(team_id=12)

        session.scalar.return_value = mem

        def _get(model, pk):
            if model is Team and pk == 12:
                return buffalo
            return None

        session.get.side_effect = _get

        with patch(
            "app.services.discord_events._resolve_league_team_for_news",
            return_value=None,
        ):
            team = resolve_news_article_team(session, article)

        self.assertIs(team, buffalo)
        session.scalar.assert_called_once()
        args = session.scalar.call_args[0][0]
        self.assertTrue(hasattr(args, "whereclause"))

    def test_admin_news_enrichment_tags_article_team_not_author(self) -> None:
        from app.models import Team

        session = MagicMock()
        detroit = SimpleNamespace(
            id=5,
            fhm_team_id="9",
            abbreviation="DET",
            name="Detroit",
            slug="detroit-red-wings",
            full_display_name=lambda: "Detroit Red Wings",
        )
        article = SimpleNamespace(
            id=800,
            league_slug="bowl-cap",
            team_id=5,
            author_user_id=42,
            title="Detroit Makes It Official with D Liles",
            body="Detroit story.",
            image_rel_path="",
        )
        mem = SimpleNamespace(team_id=28)

        def _get(model, pk):
            if model is NewsArticle and pk == 800:
                return article
            if model is Team and pk == 5:
                return detroit
            return None

        session.get.side_effect = _get
        session.scalar.return_value = mem

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
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@detroit-gm>",
            ) as by_team,
        ):
            payload = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="admin_news_published",
                payload={"article_id": 800},
            )

        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=5)
        self.assertEqual(payload["team_id"], 5)
        self.assertEqual(payload["team_abbrev"], "DET")
        self.assertEqual(payload["team_gm_mention"], "<@detroit-gm>")

    def test_gm_news_enrichment_buffalo_not_dallas_on_pk_fhm_collision(self) -> None:
        from app.models import Team

        session = MagicMock()
        buffalo = SimpleNamespace(
            id=12,
            fhm_team_id="17",
            abbreviation="BUF",
            name="Buffalo",
            slug="buffalo-sabres",
            full_display_name=lambda: "Buffalo Sabres",
        )
        article = SimpleNamespace(
            id=700,
            league_slug="bowl-cap",
            team_id=12,
            author_user_id=42,
            title="Sabres' Hull notches 500 career goals",
            body="BUFFALO, NY — Buffalo Sabres forward Brett Hull reached a major career milestone.",
            image_rel_path="",
        )
        mem = SimpleNamespace(team_id=12)

        def _get(model, pk):
            if model is NewsArticle and pk == 700:
                return article
            if model is Team and pk == 12:
                return buffalo
            return None

        session.get.side_effect = _get
        session.scalar.return_value = mem

        with (
            patch(
                "app.services.discord_events.team_fields_for_discord",
                return_value={"team_id": 12, "fhm_team_id": 17, "team_abbrev": "BUF"},
            ),
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@buffalo-gm>",
            ) as by_team,
        ):
            payload = enrich_discord_payload_for_bot(
                session,
                league_slug="bowl-cap",
                event_key="gm_news_published",
                payload={
                    "article_id": 700,
                    "team_id": 8,
                    "fhm_team_id": 12,
                    "team_abbrev": "DAL",
                    "team_gm_mention": "<@dallas-gm>",
                },
            )

        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=12)
        self.assertEqual(payload["team_id"], 12)
        self.assertEqual(payload["team_abbrev"], "BUF")
        self.assertEqual(payload["team_gm_mention"], "<@buffalo-gm>")

    def test_ensure_mention_uses_article_team_not_stale_payload_team_id(self) -> None:
        from app.models import Team

        session = MagicMock()
        detroit = SimpleNamespace(
            id=5,
            fhm_team_id="9",
            abbreviation="DET",
            name="Detroit",
            slug="detroit-red-wings",
            full_display_name=lambda: "Detroit Red Wings",
        )
        article = SimpleNamespace(
            id=900,
            league_slug="bowl-cap",
            team_id=5,
            author_user_id=42,
            title="Hockey Career Over for Detroit's Doug Brown",
            body="Detroit story.",
            image_rel_path="",
        )
        mem = SimpleNamespace(team_id=28)

        def _get(model, pk):
            if model is NewsArticle and pk == 900:
                return article
            if model is Team and pk == 5:
                return detroit
            return None

        session.get.side_effect = _get
        session.scalar.return_value = mem

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
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@detroit-gm>",
            ) as by_team,
        ):
            from app.services.discord_events import _ensure_team_gm_mention_for_payload

            payload = _ensure_team_gm_mention_for_payload(
                session,
                league_slug="bowl-cap",
                payload={
                    "article_id": 900,
                    "team_id": 28,
                    "fhm_team_id": 227,
                    "team_abbrev": "ATL",
                    "team_gm_mention": "<@atl-gm>",
                },
            )

        by_team.assert_called_once_with(session, league_slug="bowl-cap", team_id=5)
        self.assertEqual(payload["team_id"], 5)
        self.assertEqual(payload["fhm_team_id"], 9)
        self.assertEqual(payload["team_abbrev"], "DET")
        self.assertEqual(payload["team_gm_mention"], "<@detroit-gm>")

    def test_resolve_news_article_team_does_not_fallback_when_team_id_set(self) -> None:
        session = MagicMock()
        article = SimpleNamespace(
            league_slug="bowl-cap",
            team_id=5,
            author_user_id=42,
        )
        mem = SimpleNamespace(team_id=28)
        session.scalar.return_value = mem

        with patch(
            "app.services.discord_events._resolve_league_team_for_news",
            return_value=None,
        ):
            team = resolve_news_article_team(session, article)

        self.assertIsNone(team)
        session.scalar.assert_not_called()

    def test_franchise_mention_prefers_fhm_membership_over_stale_team_pk(self) -> None:
        session = MagicMock()
        detroit = SimpleNamespace(id=5, fhm_team_id="9", abbreviation="DET")

        with (
            patch(
                "app.services.discord_events._discord_user_mention_for_fhm_team",
                return_value="<@detroit-gm>",
            ) as by_fhm,
            patch(
                "app.services.discord_events._discord_user_mention_for_team",
                return_value="<@wrong-gm>",
            ) as by_team,
        ):
            from app.services.discord_events import _discord_user_mention_for_franchise

            mention = _discord_user_mention_for_franchise(
                session, league_slug="bowl-cap", team=detroit
            )

        self.assertEqual(mention, "<@detroit-gm>")
        by_fhm.assert_called_once_with(
            session, league_slug="bowl-cap", fhm_team_id="9"
        )
        by_team.assert_not_called()


if __name__ == "__main__":
    unittest.main()
