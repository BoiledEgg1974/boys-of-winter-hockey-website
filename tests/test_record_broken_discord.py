"""Broken-records Discord detection and message formatting."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.discord_events import (
    DEFAULT_EVENT_CHANNEL_KEY,
    DEFAULT_EVENT_KEYS,
    DEFAULT_EVENT_LABELS,
)
from app.services.game_records import GameRecordBreak, GameRecordHolder, GameRecordMetric
from app.services.record_broken_discord import (
    RECORD_BROKEN_EVENT_KEY,
    RecordHolderState,
    detect_snapshot_breaks,
    format_game_holder_line,
)
from scripts.league_discord_bot.formatters import format_discord_messages


def _holder(
    *,
    key: str,
    value: float,
    entity: str = "player:1",
    higher_is_better: bool = True,
    display_line: str = "",
) -> RecordHolderState:
    return RecordHolderState(
        snapshot_key=key,
        record_category="season",
        record_title="League Season Record — Goals (Regular Season)",
        record_scope="league",
        value=value,
        display_value=str(int(value) if float(value).is_integer() else value),
        display_line=display_line or f"Player — {value}",
        entity_key=entity,
        higher_is_better=higher_is_better,
        team_id=10,
        team_abbrev="MTL",
        team_name="Montreal Canadiens",
        player_id=1,
        player_name="Player",
        record_path="/season-records",
    )


class RecordBrokenRouteRegistryTest(unittest.TestCase):
    def test_record_broken_route_registered(self) -> None:
        self.assertIn(RECORD_BROKEN_EVENT_KEY, DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY[RECORD_BROKEN_EVENT_KEY], "broken-records")
        self.assertIn(RECORD_BROKEN_EVENT_KEY, DEFAULT_EVENT_LABELS)


class DetectSnapshotBreaksTest(unittest.TestCase):
    def test_seed_only_when_previous_missing(self) -> None:
        current = {"season:league:rs:goals": _holder(key="season:league:rs:goals", value=50)}
        breaks = detect_snapshot_breaks({}, current)
        self.assertEqual(breaks, [])

    def test_strict_beat_queues_break(self) -> None:
        key = "season:league:rs:goals"
        previous = {key: _holder(key=key, value=50, entity="player:1")}
        current = {key: _holder(key=key, value=55, entity="player:2", display_line="New — 55")}
        breaks = detect_snapshot_breaks(previous, current)
        self.assertEqual(len(breaks), 1)
        old, new = breaks[0]
        self.assertEqual(old.value, 50)
        self.assertEqual(new.value, 55)

    def test_tie_or_worse_does_not_break(self) -> None:
        key = "season:league:rs:goals"
        previous = {key: _holder(key=key, value=50, entity="player:1")}
        same = detect_snapshot_breaks(previous, {key: _holder(key=key, value=50, entity="player:9")})
        self.assertEqual(same, [])
        worse = detect_snapshot_breaks(previous, {key: _holder(key=key, value=49, entity="player:9")})
        self.assertEqual(worse, [])

    def test_lower_is_better_strict_beat(self) -> None:
        key = "team:fewest-points"
        previous = {key: _holder(key=key, value=40, higher_is_better=False, entity="team:1")}
        current = {key: _holder(key=key, value=35, higher_is_better=False, entity="team:2")}
        breaks = detect_snapshot_breaks(previous, current)
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0][1].value, 35)

    def test_franchise_key_independent_of_league_key(self) -> None:
        league = "season:league:rs:goals"
        franchise = "season:franchise:12:rs:goals"
        previous = {
            league: _holder(key=league, value=50),
            franchise: _holder(key=franchise, value=30, entity="player:3"),
        }
        current = {
            league: _holder(key=league, value=50),
            franchise: _holder(key=franchise, value=40, entity="player:4"),
        }
        breaks = detect_snapshot_breaks(previous, current)
        self.assertEqual(len(breaks), 1)
        self.assertEqual(breaks[0][1].snapshot_key, franchise)


class FormatGameHolderLineTest(unittest.TestCase):
    def test_formats_player_team_opp_season(self) -> None:
        metric = GameRecordMetric("goals", "Goals", "skater")
        player = SimpleNamespace(full_name="Connor McDavid", first_name="Connor", last_name="McDavid")
        team = SimpleNamespace(abbreviation="EDM", name="Oilers", id=1, fhm_team_id="22", full_display_name=lambda: "Edmonton Oilers")
        opp = SimpleNamespace(abbreviation="CGY", name="Flames", id=2, fhm_team_id="20", full_display_name=lambda: "Calgary Flames")
        holder = GameRecordHolder(
            metric=metric,
            value=6.0,
            display_value="6",
            player=player,  # type: ignore[arg-type]
            team=team,  # type: ignore[arg-type]
            opponent_team=opp,  # type: ignore[arg-type]
            game_date=date(2025, 1, 2),
            season_label="2024–25",
            game_id=99,
            source="boxscore",
        )
        line = format_game_holder_line(holder)
        self.assertIn("Connor McDavid (EDM)", line)
        self.assertIn("6 vs CGY", line)
        self.assertIn("2024–25", line)


class RecordBrokenFormatterTest(unittest.TestCase):
    def test_message_includes_team_gm_old_and_new(self) -> None:
        parts = format_discord_messages(
            {
                "event_key": "record_broken",
                "league_slug": "bowl-historical",
                "payload": {
                    "record_title": "Game Record — Goals (Regular Season, All Players)",
                    "title": "Game Record — Goals (Regular Season, All Players)",
                    "old_record_line": "Wayne Gretzky (EDM) — 5 vs TOR · 1981–82",
                    "new_record_line": "Connor McDavid (EDM) — 6 vs CGY · 2025–26",
                    "team_abbrev": "EDM",
                    "team_name": "Edmonton Oilers",
                    "fhm_team_id": 22,
                    "team_gm_mention": "<@123456789012345678>",
                    "record_url": "https://www.bowlhockey.com/bowl-historical/game-records",
                },
            },
            max_parts=1,
        )
        self.assertEqual(len(parts), 1)
        content = str(parts[0].get("content") or "")
        self.assertIn("<@123456789012345678>", content)
        self.assertIn("**Game Record — Goals (Regular Season, All Players)**", content)
        self.assertIn("Old: Wayne Gretzky (EDM) — 5 vs TOR · 1981–82", content)
        self.assertIn("New: Connor McDavid (EDM) — 6 vs CGY · 2025–26", content)
        self.assertIn("https://www.bowlhockey.com/bowl-historical/game-records", content)
        # Team label / emote line present for EDM
        self.assertTrue("EDM" in content)


class GameRecordBreakCollectionTest(unittest.TestCase):
    def test_breaks_out_only_when_existing_baseline_beaten(self) -> None:
        from app.services.game_records import _should_promote_baseline

        metric = GameRecordMetric("goals", "Goals", "skater")
        old = GameRecordHolder(
            metric=metric,
            value=5.0,
            display_value="5",
            player=SimpleNamespace(id=1, full_name="A"),  # type: ignore[arg-type]
            team=None,
            opponent_team=None,
            game_date=None,
            season_label="1981-82",
            game_id=1,
            source="baseline",
        )
        new = GameRecordHolder(
            metric=metric,
            value=6.0,
            display_value="6",
            player=SimpleNamespace(id=2, full_name="B"),  # type: ignore[arg-type]
            team=None,
            opponent_team=None,
            game_date=None,
            season_label="2025-26",
            game_id=2,
            source="boxscore",
        )
        self.assertTrue(_should_promote_baseline(old, new, metric))
        self.assertTrue(_should_promote_baseline(None, new, metric))

        br = GameRecordBreak(
            snapshot_key="game:rs:all:skater:goals",
            metric=metric,
            segment="rs",
            scope="all",
            old_holder=old,
            new_holder=new,
        )
        self.assertEqual(br.snapshot_key, "game:rs:all:skater:goals")
        self.assertEqual(br.old_holder.value, 5.0)
        self.assertEqual(br.new_holder.value, 6.0)


class NotifyIdempotencyTest(unittest.TestCase):
    def test_enqueue_uses_source_idempotency(self) -> None:
        from app.services.record_broken_discord import enqueue_record_broken_event

        session = MagicMock()
        with patch(
            "app.services.record_broken_discord.is_discord_event_route_active",
            return_value=True,
        ), patch(
            "app.services.record_broken_discord.enqueue_discord_event",
            return_value=object(),
        ) as enqueue:
            ok = enqueue_record_broken_event(
                session,
                league_slug="bowl-historical",
                payload={"title": "x"},
                source_id="season:league:rs:goals:player:2:55.0",
            )
        self.assertTrue(ok)
        enqueue.assert_called_once()
        kwargs = enqueue.call_args.kwargs
        self.assertEqual(kwargs["event_key"], "record_broken")
        self.assertEqual(kwargs["source_type"], "record_broken")
        self.assertEqual(kwargs["source_id"], "season:league:rs:goals:player:2:55.0")


if __name__ == "__main__":
    unittest.main()
