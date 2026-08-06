"""GM box score Discord builder, enqueue, and delivery override."""
from __future__ import annotations

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.discord_events import (
    DEFAULT_EVENT_CHANNEL_KEY,
    DEFAULT_EVENT_KEYS,
    DEFAULT_EVENT_LABELS,
    GM_BOX_SCORE_EVENT_KEY,
    _payload_discord_channel_id,
    serialize_pending_events_for_bot,
)
from app.services.gm_box_score_discord import (
    build_gm_box_score_text,
    drain_stashed_newly_final_game_ids,
    enqueue_gm_box_scores_for_games,
    stash_newly_final_game_ids,
)
from scripts.league_discord_bot.formatters import format_discord_messages


class GmBoxScoreDiscordTests(unittest.TestCase):
    def setUp(self) -> None:
        drain_stashed_newly_final_game_ids()

    def tearDown(self) -> None:
        drain_stashed_newly_final_game_ids()

    def test_default_route_seeded(self) -> None:
        self.assertIn(GM_BOX_SCORE_EVENT_KEY, DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY[GM_BOX_SCORE_EVENT_KEY], "gm-box-scores")
        self.assertIn(GM_BOX_SCORE_EVENT_KEY, DEFAULT_EVENT_LABELS)

    def test_payload_discord_channel_id_override(self) -> None:
        self.assertEqual(
            _payload_discord_channel_id({"discord_channel_id": "123456789012345678"}),
            "123456789012345678",
        )
        self.assertEqual(_payload_discord_channel_id({"discord_channel_id": "nope"}), "")
        self.assertEqual(_payload_discord_channel_id({}), "")

    def test_serialize_pending_prefers_payload_channel_id(self) -> None:
        session = MagicMock()
        row = SimpleNamespace(
            id=11,
            league_slug="bowl-cap",
            event_key=GM_BOX_SCORE_EVENT_KEY,
            channel_key="gm-box-scores",
            payload_json=json.dumps(
                {
                    "title": "Boston Bruins Box Score",
                    "body": "Body text",
                    "discord_channel_id": "111122223333444455",
                }
            ),
            idempotency_key="abc",
            attempts=0,
            created_at=None,
        )
        route = SimpleNamespace(
            event_key=GM_BOX_SCORE_EVENT_KEY,
            discord_channel_id="999988887777666655",
            channel_key="gm-box-scores",
            is_enabled=True,
        )
        bot_cfg = SimpleNamespace(guild_id="555544443333222211", is_enabled=True)

        with (
            patch(
                "app.services.discord_events._route_map",
                return_value={GM_BOX_SCORE_EVENT_KEY: route},
            ),
            patch(
                "app.services.discord_events.get_league_bot_config",
                return_value=bot_cfg,
            ),
            patch(
                "app.services.discord_events.enrich_discord_payload_for_bot",
                side_effect=lambda session, league_slug, event_key, payload: dict(payload),
            ),
            patch(
                "app.services.discord_events._ensure_team_gm_mention_for_payload",
                side_effect=lambda session, league_slug, payload: dict(payload),
            ),
            patch(
                "app.services.discord_events.sanitize_discord_event_payload",
                side_effect=lambda league_slug, payload: dict(payload),
            ),
        ):
            out = serialize_pending_events_for_bot(
                session, league_slug="bowl-cap", rows=[row]
            )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["discord_channel_id"], "111122223333444455")

    def test_build_text_includes_enriched_sections(self) -> None:
        home = SimpleNamespace(
            id=3,
            name="Boston",
            nickname="Bruins",
            abbreviation="BOS",
            full_display_name=lambda: "Boston Bruins",
        )
        away = SimpleNamespace(
            id=20,
            name="San Jose",
            nickname="Sharks",
            abbreviation="SJS",
            full_display_name=lambda: "San Jose Sharks",
        )
        martel = SimpleNamespace(id=1, full_name="Joël Martel", position="C", fhm_player_id="101")
        bolton = SimpleNamespace(id=2, full_name="Willie Bolton", position="G", fhm_player_id="102")
        cook = SimpleNamespace(id=3, full_name="Dave Cook", position="C", fhm_player_id="103")

        skater_home = SimpleNamespace(
            player_id=1,
            team_id=3,
            goals=1,
            assists=1,
            plus_minus=1,
            game_rating=75,
            toi_seconds=932,
            blocked_shots=2,
        )
        skater_away = SimpleNamespace(
            player_id=3,
            team_id=20,
            goals=1,
            assists=0,
            plus_minus=0,
            game_rating=38.6,
            toi_seconds=605,
            blocked_shots=1,
        )
        goalie_home = SimpleNamespace(
            player_id=2,
            team_id=3,
            saves=34,
            shots_against=35,
            goals_allowed=1,
            toi_seconds=3600,
        )
        scoring = SimpleNamespace(
            period=1,
            time_elapsed="12:34",
            scorer_player_id=1,
            assist1_player_id=None,
            assist2_player_id=None,
            scoring_team_id=3,
            strength="even",
        )
        game = SimpleNamespace(
            id=99,
            home_team_id=3,
            away_team_id=20,
            home_score=4,
            away_score=1,
            game_date=date(2043, 12, 2),
            status="final",
            went_to_overtime=False,
            went_to_shootout=False,
            home_shots=30,
            away_shots=35,
            pp_goals_home=1,
            pp_opp_home=6,
            pp_goals_away=0,
            pp_opp_away=5,
            hits_home=22,
            hits_away=18,
            fhm_star1_player_id="101",
            fhm_star2_player_id="102",
            fhm_star3_player_id="103",
            skater_lines=[skater_home, skater_away],
            goalie_lines=[goalie_home],
            scoring_events=[scoring],
            score_away_p1=None,
            score_home_p1=None,
            score_away_p2=None,
            score_home_p2=None,
            score_away_p3=None,
            score_home_p3=None,
            score_away_ot=None,
            score_home_ot=None,
        )

        players = {1: martel, 2: bolton, 3: cook}
        teams = {3: home, 20: away}

        session = MagicMock()

        def _get(model, pk):
            if model is None:
                return None
            name = getattr(model, "__name__", str(model))
            if "Player" in name:
                return players.get(int(pk))
            if "Team" in name:
                return teams.get(int(pk))
            return None

        session.get.side_effect = _get

        def _scalar(stmt):
            # star lookup by fhm id
            return None

        session.scalars.side_effect = lambda stmt: MagicMock(
            first=lambda: martel
            if "101" in str(stmt)
            else (bolton if "102" in str(stmt) else (cook if "103" in str(stmt) else None)),
            all=lambda: [],
        )

        with patch(
            "app.services.gm_box_score_discord.build_league_public_url",
            return_value="https://www.bowlhockey.com/bowl-cap/game/99",
        ), patch(
            "app.services.gm_box_score_discord._star_labels",
            return_value=["C Joël Martel", "G Willie Bolton", "C Dave Cook"],
        ):
            text = build_gm_box_score_text(
                session, game=game, recipient_team=home, league_slug="bowl-cap"
            )

        self.assertIn("Boston Bruins Box Score", text)
        self.assertIn("Wednesday, December 2, 2043", text)
        self.assertIn("San Jose Sharks 1 @ Boston Bruins 4", text)
        self.assertIn("Periods:", text)
        self.assertIn("Shots:", text)
        self.assertIn("Special Teams:", text)
        self.assertIn("Physical:", text)
        self.assertIn("Stars:", text)
        self.assertIn("Goalies", text)
        self.assertIn("Top Performances", text)
        self.assertIn("Scoring Summary", text)
        self.assertIn("Full Box Score", text)

    def test_enqueue_home_and_away_skips_blank_channel(self) -> None:
        site = MagicMock()
        league = MagicMock()
        home = SimpleNamespace(
            id=3,
            name="Boston",
            nickname="Bruins",
            abbreviation="BOS",
            full_display_name=lambda: "Boston Bruins",
            fhm_team_id="5",
            slug="bos",
        )
        away = SimpleNamespace(
            id=20,
            name="San Jose",
            nickname="Sharks",
            abbreviation="SJS",
            full_display_name=lambda: "San Jose Sharks",
            fhm_team_id="22",
            slug="sjs",
        )
        game = SimpleNamespace(
            id=50,
            home_team_id=3,
            away_team_id=20,
            home_score=4,
            away_score=1,
            status="final",
            game_date=date(2043, 12, 2),
            went_to_overtime=False,
            went_to_shootout=False,
            home_shots=30,
            away_shots=35,
            pp_goals_home=1,
            pp_opp_home=6,
            pp_goals_away=0,
            pp_opp_away=5,
            hits_home=10,
            hits_away=8,
            fhm_star1_player_id=None,
            fhm_star2_player_id=None,
            fhm_star3_player_id=None,
            skater_lines=[],
            goalie_lines=[],
            scoring_events=[],
        )

        def _get(model, pk):
            if int(pk) == 50:
                return game
            if int(pk) == 3:
                return home
            if int(pk) == 20:
                return away
            return None

        league.get.side_effect = _get
        created_rows = []

        def _enqueue(*args, **kwargs):
            row = SimpleNamespace(status="pending", attempts=0, sent_at=None, id=len(created_rows) + 1)
            created_rows.append((kwargs.get("source_id"), kwargs["payload"].get("discord_channel_id")))
            return row

        with (
            patch(
                "app.services.gm_box_score_discord.discord_team_channel_map",
                return_value={3: "111122223333444455"},  # only Boston
            ),
            patch(
                "app.services.gm_box_score_discord.enqueue_discord_event",
                side_effect=_enqueue,
            ),
            patch(
                "app.services.gm_box_score_discord.build_league_public_url",
                return_value="https://example.com/bowl-cap/game/50",
            ),
            patch(
                "app.services.gm_box_score_discord.team_fields_for_discord",
                return_value={"team_id": 3, "team_abbrev": "BOS"},
            ),
        ):
            stats = enqueue_gm_box_scores_for_games(
                site, league, league_slug="bowl-cap", game_ids={50}
            )

        self.assertEqual(stats["games"], 1)
        self.assertEqual(stats["queued"], 1)
        self.assertEqual(stats["skipped_no_channel"], 1)
        self.assertEqual(created_rows, [("50:3", "111122223333444455")])

    def test_enqueue_idempotent_source_ids(self) -> None:
        site = MagicMock()
        league = MagicMock()
        home = SimpleNamespace(
            id=3,
            full_display_name=lambda: "Boston Bruins",
            abbreviation="BOS",
            name="Boston",
            nickname="Bruins",
            fhm_team_id="5",
            slug="bos",
        )
        away = SimpleNamespace(
            id=20,
            full_display_name=lambda: "San Jose Sharks",
            abbreviation="SJS",
            name="San Jose",
            nickname="Sharks",
            fhm_team_id="22",
            slug="sjs",
        )
        game = SimpleNamespace(
            id=50,
            home_team_id=3,
            away_team_id=20,
            home_score=4,
            away_score=1,
            status="final",
            game_date=date(2043, 12, 2),
            went_to_overtime=False,
            went_to_shootout=False,
            home_shots=30,
            away_shots=35,
            pp_goals_home=0,
            pp_opp_home=0,
            pp_goals_away=0,
            pp_opp_away=0,
            hits_home=None,
            hits_away=None,
            fhm_star1_player_id=None,
            fhm_star2_player_id=None,
            fhm_star3_player_id=None,
            skater_lines=[],
            goalie_lines=[],
            scoring_events=[],
        )

        def _get(model, pk):
            return {50: game, 3: home, 20: away}.get(int(pk))

        league.get.side_effect = _get
        calls: list[str] = []

        def _enqueue(*args, **kwargs):
            sid = str(kwargs.get("source_id") or "")
            if sid in calls:
                return SimpleNamespace(status="sent", attempts=1, sent_at=object(), id=9)
            calls.append(sid)
            return SimpleNamespace(status="pending", attempts=0, sent_at=None, id=len(calls))

        with (
            patch(
                "app.services.gm_box_score_discord.discord_team_channel_map",
                return_value={3: "111122223333444455", 20: "999988887777666655"},
            ),
            patch(
                "app.services.gm_box_score_discord.enqueue_discord_event",
                side_effect=_enqueue,
            ),
            patch(
                "app.services.gm_box_score_discord.build_league_public_url",
                return_value="https://example.com/g/50",
            ),
            patch(
                "app.services.gm_box_score_discord.team_fields_for_discord",
                return_value={},
            ),
        ):
            first = enqueue_gm_box_scores_for_games(
                site, league, league_slug="bowl-cap", game_ids={50}
            )
            second = enqueue_gm_box_scores_for_games(
                site, league, league_slug="bowl-cap", game_ids={50}
            )

        self.assertEqual(first["queued"], 2)
        self.assertEqual(second["queued"], 0)
        self.assertEqual(sorted(calls), ["50:20", "50:3"])

    def test_stash_and_drain_newly_final_ids(self) -> None:
        stash_newly_final_game_ids({1, 2, 2})
        stash_newly_final_game_ids([3])
        self.assertEqual(drain_stashed_newly_final_game_ids(), {1, 2, 3})
        self.assertEqual(drain_stashed_newly_final_game_ids(), set())

    def test_formatter_posts_body_text(self) -> None:
        body = (
            "**Boston Bruins Box Score** — Wednesday, December 2, 2043\n"
            "**San Jose Sharks 1 @ Boston Bruins 4**\n\n"
            "**Shots:** San Jose Sharks 35 - Boston Bruins 30"
        )
        messages = format_discord_messages(
            {
                "event_key": GM_BOX_SCORE_EVENT_KEY,
                "league_slug": "bowl-cap",
                "payload": {
                    "title": "Boston Bruins Box Score",
                    "body": body,
                    "body_preview": body[:80],
                },
            }
        )
        self.assertTrue(messages)
        content = str(messages[0].get("content") or "")
        self.assertIn("Boston Bruins Box Score", content)
        self.assertIn("San Jose Sharks 1 @ Boston Bruins 4", content)


if __name__ == "__main__":
    unittest.main()
