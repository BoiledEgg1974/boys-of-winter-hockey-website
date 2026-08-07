"""Per-team game boxscore Discord routes, enqueue, and formatter."""

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
    GAME_BOXSCORE_EVENT_KEY,
)
from app.services.game_boxscore_discord import (
    build_game_boxscore_discord_payload,
    drain_stashed_newly_final_game_ids,
    enqueue_game_boxscore_events_for_game,
    notify_game_boxscores_after_import,
    stash_newly_final_game_ids,
)
from scripts.league_discord_bot.formatters import format_discord_messages


class GameBoxscoreRouteRegistryTest(unittest.TestCase):
    def test_game_boxscore_route_registered(self) -> None:
        self.assertIn(GAME_BOXSCORE_EVENT_KEY, DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY[GAME_BOXSCORE_EVENT_KEY], "boxscores")
        self.assertIn(GAME_BOXSCORE_EVENT_KEY, DEFAULT_EVENT_LABELS)
        self.assertIn("per team", DEFAULT_EVENT_LABELS[GAME_BOXSCORE_EVENT_KEY].lower())


class StashDrainTest(unittest.TestCase):
    def tearDown(self) -> None:
        drain_stashed_newly_final_game_ids()

    def test_stash_and_drain(self) -> None:
        drain_stashed_newly_final_game_ids()
        self.assertEqual(stash_newly_final_game_ids({3, 5, 5}), 2)
        self.assertEqual(drain_stashed_newly_final_game_ids(), {3, 5})
        self.assertEqual(drain_stashed_newly_final_game_ids(), set())


class EnsureTeamChannelsTest(unittest.TestCase):
    def test_creates_missing_rows_for_standing_teams(self) -> None:
        from app.services.discord_events import ensure_game_boxscore_team_channels
        from app.site_models import DiscordTeamChannelRoute

        site = MagicMock()
        league = MagicMock()
        existing = SimpleNamespace(team_id=1, discord_channel_id="111")
        site.scalars.return_value.all.return_value = [existing]

        with patch(
            "app.services.discord_events.ensure_discord_routes"
        ), patch(
            "app.services.discord_events._active_team_ids_for_boxscore_channels",
            return_value=[1, 2, 3],
        ), patch("app.sqlite_retry.commit_with_sqlite_retry") as commit:
            created = ensure_game_boxscore_team_channels(site, league, "bowl-historical")

        self.assertEqual(created, 2)
        self.assertEqual(site.add.call_count, 2)
        added_ids = sorted(int(c.args[0].team_id) for c in site.add.call_args_list)
        self.assertEqual(added_ids, [2, 3])
        for call in site.add.call_args_list:
            row = call.args[0]
            self.assertIsInstance(row, DiscordTeamChannelRoute)
            self.assertEqual(row.event_key, GAME_BOXSCORE_EVENT_KEY)
            self.assertEqual(row.discord_channel_id, "")
        commit.assert_called_once()

    def test_expansion_adds_new_row_without_deleting_old(self) -> None:
        from app.services.discord_events import ensure_game_boxscore_team_channels

        site = MagicMock()
        league = MagicMock()
        existing = [
            SimpleNamespace(team_id=1, discord_channel_id="111"),
            SimpleNamespace(team_id=2, discord_channel_id="222"),
        ]
        site.scalars.return_value.all.return_value = existing

        with patch(
            "app.services.discord_events.ensure_discord_routes"
        ), patch(
            "app.services.discord_events._active_team_ids_for_boxscore_channels",
            return_value=[1, 2, 99],
        ), patch("app.sqlite_retry.commit_with_sqlite_retry"):
            created = ensure_game_boxscore_team_channels(site, league, "bowl-cap")

        self.assertEqual(created, 1)
        self.assertEqual(int(site.add.call_args.args[0].team_id), 99)


class ResolveTeamChannelTest(unittest.TestCase):
    def test_resolve_uses_team_row_not_master_channel(self) -> None:
        from app.services.discord_events import resolve_game_boxscore_team_channel_id

        site = MagicMock()
        master = SimpleNamespace(is_enabled=True, discord_channel_id="999999999999999999")
        team_row = SimpleNamespace(
            is_enabled=True, discord_channel_id="1229811680930955496"
        )
        bot_cfg = SimpleNamespace(is_enabled=True)

        with patch(
            "app.services.discord_events.ensure_discord_routes"
        ), patch(
            "app.services.discord_events._route_map",
            return_value={GAME_BOXSCORE_EVENT_KEY: master},
        ), patch(
            "app.services.discord_events.get_league_bot_config",
            return_value=bot_cfg,
        ):
            site.scalar.return_value = team_row
            cid = resolve_game_boxscore_team_channel_id(
                site, league_slug="bowl-historical", team_id=12
            )
        self.assertEqual(cid, "1229811680930955496")

    def test_blank_team_channel_returns_empty(self) -> None:
        from app.services.discord_events import resolve_game_boxscore_team_channel_id

        site = MagicMock()
        master = SimpleNamespace(is_enabled=True, discord_channel_id="")
        team_row = SimpleNamespace(is_enabled=True, discord_channel_id="")
        bot_cfg = SimpleNamespace(is_enabled=True)

        with patch(
            "app.services.discord_events.ensure_discord_routes"
        ), patch(
            "app.services.discord_events._route_map",
            return_value={GAME_BOXSCORE_EVENT_KEY: master},
        ), patch(
            "app.services.discord_events.get_league_bot_config",
            return_value=bot_cfg,
        ):
            site.scalar.return_value = team_row
            cid = resolve_game_boxscore_team_channel_id(
                site, league_slug="bowl-historical", team_id=12
            )
        self.assertEqual(cid, "")


class SerializePendingBoxscoreTest(unittest.TestCase):
    def test_serialize_picks_team_channel_from_payload(self) -> None:
        from app.services.discord_events import serialize_pending_events_for_bot

        site = MagicMock()
        row = SimpleNamespace(
            id=7,
            league_slug="bowl-historical",
            event_key=GAME_BOXSCORE_EVENT_KEY,
            channel_key="boxscores",
            idempotency_key="abc",
            payload_json=json.dumps({"team_id": 5, "title": "Final", "game_url": "https://x/g/1"}),
            attempts=0,
            created_at=None,
        )
        routes = {
            GAME_BOXSCORE_EVENT_KEY: SimpleNamespace(
                discord_channel_id="", channel_key="boxscores", is_enabled=True
            )
        }
        bot_cfg = SimpleNamespace(guild_id="1", is_enabled=True)

        with patch(
            "app.services.discord_events._route_map", return_value=routes
        ), patch(
            "app.services.discord_events.get_league_bot_config", return_value=bot_cfg
        ), patch(
            "app.services.discord_events.enrich_discord_payload_for_bot",
            side_effect=lambda *a, **k: k["payload"],
        ), patch(
            "app.services.discord_events._ensure_team_gm_mention_for_payload",
            side_effect=lambda **k: k["payload"],
        ), patch(
            "app.services.discord_events.sanitize_discord_event_payload",
            side_effect=lambda _slug, payload: payload,
        ), patch(
            "app.services.discord_events.resolve_game_boxscore_team_channel_id",
            return_value="1555555555555555555",
        ):
            out = serialize_pending_events_for_bot(
                site, league_slug="bowl-historical", rows=[row]
            )

        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["discord_channel_id"], "1555555555555555555")
        self.assertEqual(out[0]["event_key"], GAME_BOXSCORE_EVENT_KEY)


class EnqueueBoxscoreTest(unittest.TestCase):
    def tearDown(self) -> None:
        drain_stashed_newly_final_game_ids()

    def test_enqueues_both_teams_with_distinct_source_ids(self) -> None:
        home = SimpleNamespace(
            id=10,
            abbreviation="MTL",
            name="Montreal",
            fhm_team_id="1",
            slug="mtl",
            full_display_name=lambda: "Montreal Canadiens",
        )
        away = SimpleNamespace(
            id=11,
            abbreviation="TOR",
            name="Toronto",
            fhm_team_id="2",
            slug="tor",
            full_display_name=lambda: "Toronto Maple Leafs",
        )
        game = SimpleNamespace(
            id=42,
            status="final",
            home_team_id=10,
            away_team_id=11,
            home_team=home,
            away_team=away,
            home_score=3,
            away_score=2,
            game_date=date(2025, 10, 12),
            game_type="0",
            went_to_overtime=False,
            went_to_shootout=False,
            fhm_star1_player_id=None,
            fhm_star2_player_id=None,
            fhm_star3_player_id=None,
        )
        site = MagicMock()
        league = MagicMock()
        league.get.return_value = game
        source_ids: list[str] = []

        def _enqueue(*_args, **kwargs):
            source_ids.append(str(kwargs.get("source_id") or ""))
            return SimpleNamespace(status="pending", attempts=0, id=len(source_ids))

        with patch(
            "app.services.game_boxscore_discord.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.resolve_game_boxscore_team_channel_id",
            side_effect=lambda *_a, **k: f"chan-{k['team_id']}",
        ), patch(
            "app.services.game_boxscore_discord.enqueue_discord_event",
            side_effect=_enqueue,
        ), patch(
            "app.services.game_boxscore_discord.build_league_public_url",
            return_value="https://www.bowlhockey.com/bowl-historical/game/42",
        ), patch(
            "app.services.game_boxscore_discord._load_game_skater_rows",
            return_value=[],
        ), patch(
            "app.services.game_boxscore_discord._load_game_goalie_rows",
            return_value=[],
        ):
            n = enqueue_game_boxscore_events_for_game(
                site, league, league_slug="bowl-historical", game_id=42
            )

        self.assertEqual(n, 2)
        self.assertEqual(sorted(source_ids), ["42:10", "42:11"])

    def test_skips_blank_team_channel(self) -> None:
        game = SimpleNamespace(
            id=7,
            status="final",
            home_team_id=1,
            away_team_id=2,
            home_team=None,
            away_team=None,
            home_score=1,
            away_score=0,
            game_date=None,
            game_type=None,
            went_to_overtime=False,
            went_to_shootout=False,
            fhm_star1_player_id=None,
            fhm_star2_player_id=None,
            fhm_star3_player_id=None,
        )
        site = MagicMock()
        league = MagicMock()
        league.get.return_value = game

        with patch(
            "app.services.game_boxscore_discord.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.resolve_game_boxscore_team_channel_id",
            return_value="",
        ), patch(
            "app.services.game_boxscore_discord.enqueue_discord_event"
        ) as enqueue:
            n = enqueue_game_boxscore_events_for_game(
                site, league, league_slug="bowl-cap", game_id=7
            )

        self.assertEqual(n, 0)
        enqueue.assert_not_called()

    def test_notify_keeps_pending_when_no_targets(self) -> None:
        drain_stashed_newly_final_game_ids()
        stash_newly_final_game_ids({9})
        site = MagicMock()
        league = MagicMock()

        with patch(
            "app.services.game_boxscore_discord.record_pending_game_boxscore_ids"
        ) as record, patch(
            "app.services.game_boxscore_discord.list_pending_game_boxscore_ids",
            return_value={9},
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.has_game_boxscore_delivery_target",
            return_value=False,
        ), patch(
            "app.services.game_boxscore_discord.enqueue_game_boxscore_events_for_game"
        ) as enqueue_one, patch(
            "app.services.game_boxscore_discord.clear_pending_game_boxscore_ids"
        ) as clear:
            out = notify_game_boxscores_after_import(
                league, site, league_slug="bowl-historical"
            )

        record.assert_called_once()
        enqueue_one.assert_not_called()
        clear.assert_not_called()
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["pending"], 1)

    def test_notify_clears_pending_after_queue(self) -> None:
        drain_stashed_newly_final_game_ids()
        stash_newly_final_game_ids({9})
        site = MagicMock()
        league = MagicMock()

        with patch(
            "app.services.game_boxscore_discord.record_pending_game_boxscore_ids"
        ), patch(
            "app.services.game_boxscore_discord.list_pending_game_boxscore_ids",
            return_value={9},
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.has_game_boxscore_delivery_target",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.enqueue_game_boxscore_events_for_game",
            return_value=2,
        ) as enqueue_one, patch(
            "app.services.game_boxscore_discord.clear_pending_game_boxscore_ids"
        ) as clear:
            first = notify_game_boxscores_after_import(
                league, site, league_slug="bowl-historical"
            )
            with patch(
                "app.services.game_boxscore_discord.list_pending_game_boxscore_ids",
                return_value=set(),
            ):
                second = notify_game_boxscores_after_import(
                    league, site, league_slug="bowl-historical"
                )

        self.assertEqual(first["games"], 1)
        self.assertEqual(first["queued"], 2)
        self.assertEqual(second["games"], 0)
        self.assertEqual(enqueue_one.call_count, 1)
        clear.assert_called_once()
        self.assertEqual(set(clear.call_args.kwargs["game_ids"]), {9})


class QueueRecentBoxscoresTest(unittest.TestCase):
    def test_recent_window_uses_latest_final_date(self) -> None:
        from app.services.game_boxscore_discord import recent_final_game_ids_for_boxscores

        league = MagicMock()
        league.scalar.return_value = date(2025, 10, 20)
        league.scalars.return_value.all.return_value = [101, 102]

        with patch(
            "app.services.seasons.get_current_season",
            return_value=SimpleNamespace(id=3),
        ):
            ids, start, latest = recent_final_game_ids_for_boxscores(league, days=7)

        self.assertEqual(ids, [101, 102])
        self.assertEqual(start, date(2025, 10, 14))
        self.assertEqual(latest, date(2025, 10, 20))

    def test_queue_recent_reports_missing_channels(self) -> None:
        from app.services.game_boxscore_discord import queue_recent_game_boxscores

        site = MagicMock()
        league = MagicMock()
        with patch(
            "app.services.game_boxscore_discord.recent_final_game_ids_for_boxscores",
            return_value=([1, 2], date(2025, 10, 14), date(2025, 10, 20)),
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.has_game_boxscore_delivery_target",
            return_value=False,
        ), patch(
            "app.services.game_boxscore_discord.enqueue_game_boxscore_events_for_game"
        ) as enqueue:
            out = queue_recent_game_boxscores(
                league, site, league_slug="bowl-historical", days=7
            )

        enqueue.assert_not_called()
        self.assertFalse(out["ok"])
        self.assertEqual(out["skipped"], 2)
        self.assertIn("channel", out["message"].lower())

    def test_queue_recent_enqueues_matching_games(self) -> None:
        from app.services.game_boxscore_discord import queue_recent_game_boxscores

        site = MagicMock()
        league = MagicMock()
        with patch(
            "app.services.game_boxscore_discord.recent_final_game_ids_for_boxscores",
            return_value=([9, 10], date(2025, 10, 14), date(2025, 10, 20)),
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.has_game_boxscore_delivery_target",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.enqueue_game_boxscore_events_for_game",
            side_effect=[2, 2],
        ):
            out = queue_recent_game_boxscores(
                league, site, league_slug="bowl-cap", days=7
            )

        self.assertTrue(out["ok"])
        self.assertEqual(out["games"], 2)
        self.assertEqual(out["queued"], 4)
        self.assertEqual(out["window_start"], "2025-10-14")
        self.assertEqual(out["window_end"], "2025-10-20")
        self.assertIn("Queued 4", out["message"])

    def test_force_requeue_clears_locks_then_enqueues(self) -> None:
        from app.services.game_boxscore_discord import queue_recent_game_boxscores

        site = MagicMock()
        league = MagicMock()
        game9 = SimpleNamespace(id=9, away_team_id=1, home_team_id=2)
        game10 = SimpleNamespace(id=10, away_team_id=3, home_team_id=4)
        league.get.side_effect = lambda _model, gid: {9: game9, 10: game10}.get(int(gid))

        with patch(
            "app.services.game_boxscore_discord.recent_final_game_ids_for_boxscores",
            return_value=([9, 10], date(2025, 10, 14), date(2025, 10, 20)),
        ), patch(
            "app.services.game_boxscore_discord.ensure_game_boxscore_team_channels"
        ), patch(
            "app.services.game_boxscore_discord.has_game_boxscore_delivery_target",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.game_boxscore_discord.clear_game_boxscore_delivery_locks",
            return_value={"delivered_cleared": 4, "outbound_cancelled": 4},
        ) as clear_locks, patch(
            "app.services.game_boxscore_discord.enqueue_game_boxscore_events_for_game",
            side_effect=[2, 2],
        ):
            out = queue_recent_game_boxscores(
                league, site, league_slug="bowl-historical", days=7, force=True
            )

        clear_locks.assert_called_once()
        cleared_ids = set(clear_locks.call_args.kwargs["source_ids"])
        self.assertEqual(cleared_ids, {"9:1", "9:2", "10:3", "10:4"})
        self.assertTrue(out["ok"])
        self.assertTrue(out["force"])
        self.assertEqual(out["delivered_cleared"], 4)
        self.assertEqual(out["outbound_cancelled"], 4)
        self.assertEqual(out["queued"], 4)
        self.assertIn("Force re-queue", out["message"])


class FormatterTest(unittest.TestCase):
    def test_formatter_includes_expanded_boxscore_sections(self) -> None:
        event = {
            "league_slug": "bowl-historical",
            "event_key": GAME_BOXSCORE_EVENT_KEY,
            "payload": {
                "title": "Final",
                "date": "1970-12-27",
                "game_type_label": "RS",
                "away_score": 2,
                "home_score": 3,
                "away_team": {"abbrev": "PHI", "fhm_team_id": 10},
                "home_team": {"abbrev": "STL", "fhm_team_id": 12},
                "shots": {"away": 30, "home": 21},
                "special_teams": {
                    "away_pp": "1/5",
                    "home_pp": "0/5",
                    "away_pim": 8,
                    "home_pim": 10,
                    "away_hits": 21,
                    "home_hits": 28,
                },
                "team_leaders": {
                    "away": [
                        {"name": "Ron Anderson", "line": "1G"},
                        {"name": "Bill Flett", "line": "1G"},
                    ],
                    "home": [
                        {"name": "Wayne Carleton", "line": "2G"},
                        {"name": "John Miszuk", "line": "1G"},
                    ],
                },
                "goalies": {
                    "away": [
                        {
                            "name": "Doug Favell",
                            "line": "18/21 (85.7%)",
                            "saves": 18,
                            "sa": 21,
                            "sv_pct": 85.7,
                        }
                    ],
                    "home": [
                        {
                            "name": "Ernie Wakely",
                            "line": "28/30 (93.3%)",
                            "saves": 28,
                            "sa": 30,
                            "sv_pct": 93.3,
                        }
                    ],
                },
                "stars": [
                    {"name": "Wayne Carleton", "team_abbr": "STL", "line": "2G"},
                    {"name": "Ron Anderson", "team_abbr": "PHI", "line": "1G"},
                    {"name": "John Miszuk", "team_abbr": "STL", "line": "1G"},
                ],
                "game_url": "https://www.bowlhockey.com/bowl-historical/game/867",
            },
        }
        bodies = format_discord_messages(event, max_parts=2)
        self.assertTrue(bodies)
        content = "\n".join(str(b.get("content") or "") for b in bodies)
        self.assertIn("**PHI**", content)
        self.assertIn("2 – 3", content)
        self.assertIn("**STL**", content)
        self.assertIn("Final · 1970-12-27 · RS", content)
        self.assertIn("SOG", content)
        self.assertIn("PP", content)
        self.assertIn("1/5", content)
        self.assertIn("PIM", content)
        self.assertIn("Hits", content)
        self.assertIn("**Top performers**", content)
        self.assertIn("Wayne Carleton 2G", content)
        self.assertIn("**Goalies**", content)
        self.assertIn("Ernie Wakely 28/30 (93.3%)", content)
        self.assertIn("**Three stars**", content)
        self.assertIn("★1 Wayne Carleton (STL) 2G", content)
        self.assertIn("https://www.bowlhockey.com/bowl-historical/game/867", content)
        self.assertIn("bowlhockey.com", content)

    def test_payload_builder_sets_target_team_id(self) -> None:
        home = SimpleNamespace(
            id=10,
            abbreviation="MTL",
            name="Montreal",
            fhm_team_id="8",
            slug="mtl",
            full_display_name=lambda: "Montreal Canadiens",
        )
        away = SimpleNamespace(
            id=11,
            abbreviation="TOR",
            name="Toronto",
            fhm_team_id="21",
            slug="tor",
            full_display_name=lambda: "Toronto Maple Leafs",
        )
        game = SimpleNamespace(
            id=42,
            home_team=home,
            away_team=away,
            home_team_id=10,
            away_team_id=11,
            home_score=4,
            away_score=1,
            home_shots=28,
            away_shots=22,
            pp_goals_home=1,
            pp_opp_home=3,
            pp_goals_away=0,
            pp_opp_away=4,
            pim_home=6,
            pim_away=8,
            hits_home=20,
            hits_away=18,
            game_date=date(2025, 11, 1),
            game_type="playoff",
            went_to_overtime=True,
            went_to_shootout=False,
            status="final",
            fhm_star1_player_id=None,
            fhm_star2_player_id=None,
            fhm_star3_player_id=None,
        )
        league = MagicMock()
        with patch(
            "app.services.game_boxscore_discord.build_league_public_url",
            return_value="https://example.test/game/42",
        ), patch(
            "app.services.game_boxscore_discord._load_game_skater_rows",
            return_value=[],
        ), patch(
            "app.services.game_boxscore_discord._load_game_goalie_rows",
            return_value=[],
        ):
            payload = build_game_boxscore_discord_payload(
                league,
                league_slug="bowl-historical",
                game=game,
                target_team_id=11,
            )
        self.assertEqual(payload["team_id"], 11)
        self.assertEqual(payload["away_score"], 1)
        self.assertEqual(payload["home_score"], 4)
        self.assertEqual(payload["shots"]["away"], 22)
        self.assertEqual(payload["shots"]["home"], 28)
        self.assertEqual(payload["special_teams"]["away_pp"], "0/4")
        self.assertEqual(payload["special_teams"]["home_pp"], "1/3")
        self.assertEqual(payload["special_teams"]["away_pim"], 8)
        self.assertEqual(payload["special_teams"]["home_hits"], 20)
        self.assertIn("PO", payload["game_type_label"])
        self.assertIn("OT", payload["game_type_label"])
        self.assertEqual(payload["game_url"], "https://example.test/game/42")

    def test_payload_builder_includes_leaders_and_goalies(self) -> None:
        home = SimpleNamespace(
            id=12,
            abbreviation="STL",
            name="St. Louis",
            fhm_team_id="12",
            full_display_name=lambda: "St. Louis Blues",
        )
        away = SimpleNamespace(
            id=10,
            abbreviation="PHI",
            name="Philadelphia",
            fhm_team_id="10",
            full_display_name=lambda: "Philadelphia Flyers",
        )
        game = SimpleNamespace(
            id=867,
            home_team=home,
            away_team=away,
            home_team_id=12,
            away_team_id=10,
            home_score=3,
            away_score=2,
            home_shots=21,
            away_shots=30,
            pp_goals_home=0,
            pp_opp_home=5,
            pp_goals_away=1,
            pp_opp_away=5,
            pim_home=10,
            pim_away=8,
            hits_home=28,
            hits_away=21,
            game_date=date(1970, 12, 27),
            game_type="0",
            went_to_overtime=False,
            went_to_shootout=False,
            status="final",
            fhm_star1_player_id=None,
            fhm_star2_player_id=None,
            fhm_star3_player_id=None,
        )
        skaters = [
            SimpleNamespace(
                team_id=12, player_id=1, goals=2, assists=0, game_rating=96.0
            ),
            SimpleNamespace(
                team_id=10, player_id=2, goals=1, assists=0, game_rating=70.0
            ),
            SimpleNamespace(
                team_id=12, player_id=3, goals=1, assists=0, game_rating=71.0
            ),
        ]
        goalies = [
            SimpleNamespace(
                team_id=12,
                player_id=4,
                saves=28,
                shots_against=30,
                goals_allowed=2,
                decision=None,
                toi_seconds=3600,
            ),
            SimpleNamespace(
                team_id=10,
                player_id=5,
                saves=18,
                shots_against=21,
                goals_allowed=3,
                decision=None,
                toi_seconds=3570,
            ),
            SimpleNamespace(
                team_id=12,
                player_id=6,
                saves=0,
                shots_against=0,
                goals_allowed=0,
                decision=None,
                toi_seconds=0,
            ),
        ]
        league = MagicMock()
        with patch(
            "app.services.game_boxscore_discord.build_league_public_url",
            return_value="https://example.test/game/867",
        ), patch(
            "app.services.game_boxscore_discord._load_game_skater_rows",
            return_value=skaters,
        ), patch(
            "app.services.game_boxscore_discord._load_game_goalie_rows",
            return_value=goalies,
        ), patch(
            "app.services.game_boxscore_discord._player_name_map",
            return_value={
                1: "Wayne Carleton",
                2: "Ron Anderson",
                3: "John Miszuk",
                4: "Ernie Wakely",
                5: "Doug Favell",
                6: "Backup",
            },
        ):
            payload = build_game_boxscore_discord_payload(
                league,
                league_slug="bowl-historical",
                game=game,
                target_team_id=12,
            )
        self.assertEqual(payload["shots"]["away"], 30)
        self.assertEqual(payload["shots"]["home"], 21)
        self.assertEqual(payload["team_leaders"]["home"][0]["name"], "Wayne Carleton")
        self.assertEqual(payload["team_leaders"]["home"][0]["line"], "2G")
        self.assertEqual(payload["goalies"]["away"][0]["name"], "Doug Favell")
        self.assertEqual(payload["goalies"]["away"][0]["line"], "18/21 (85.7%)")
        self.assertEqual(len(payload["goalies"]["home"]), 1)
        self.assertNotIn("Backup", payload["goalies"]["home"][0]["name"])


if __name__ == "__main__":
    unittest.main()
