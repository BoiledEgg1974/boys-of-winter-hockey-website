"""Tests for deploy-db broken-record Discord sidecars."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.deploy_discord_records import (
    clear_deploy_record_break_events,
    load_deploy_record_break_events,
    record_deploy_record_break_events,
    save_live_record_state,
    load_live_record_state,
    clear_live_record_state,
)
from app.services.record_broken_discord import (
    RecordHolderState,
    events_from_live_record_state_diff,
    notify_record_breaks_after_import,
)


def _holder(*, key: str, value: float, entity: str = "player:1") -> RecordHolderState:
    return RecordHolderState(
        snapshot_key=key,
        record_category="season",
        record_title="League Season Record — Goals (Regular Season)",
        record_scope="league",
        value=value,
        display_value=str(int(value)),
        display_line=f"Player — {value}",
        entity_key=entity,
        higher_is_better=True,
        record_path="/season-records?segment=rs",
    )


class DeployDiscordRecordsSidecarTest(unittest.TestCase):
    def test_record_merge_load_clear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            added = record_deploy_record_break_events(
                "bowl-cap",
                [
                    {"source_id": "a", "payload": {"title": "one"}},
                    {"source_id": "b", "payload": {"title": "two"}},
                ],
                instance_root=root,
            )
            self.assertEqual(added, 2)
            added = record_deploy_record_break_events(
                "bowl-cap",
                [
                    {"source_id": "b", "payload": {"title": "two-updated"}},
                    {"source_id": "c", "payload": {"title": "three"}},
                ],
                instance_root=root,
            )
            self.assertEqual(added, 1)
            events = load_deploy_record_break_events("bowl-cap", instance_root=root)
            self.assertEqual([e["source_id"] for e in events], ["a", "b", "c"])
            self.assertEqual(events[1]["payload"]["title"], "two-updated")
            self.assertTrue(clear_deploy_record_break_events("bowl-cap", instance_root=root))
            self.assertEqual(load_deploy_record_break_events("bowl-cap", instance_root=root), [])

    def test_skips_malformed_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            added = record_deploy_record_break_events(
                "bowl-historical",
                [
                    {"source_id": "", "payload": {"title": "nope"}},
                    {"source_id": "ok", "payload": "not-a-dict"},
                    {"source_id": "ok", "payload": {"title": "yes"}},
                ],
                instance_root=root,
            )
            self.assertEqual(added, 1)
            events = load_deploy_record_break_events("bowl-historical", instance_root=root)
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["source_id"], "ok")

    def test_live_state_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_live_record_state(
                "bowl-fantasy",
                {"snapshots": {"k": {"value": 1}}, "game_baselines": {}},
                instance_root=root,
            )
            loaded = load_live_record_state("bowl-fantasy", instance_root=root)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded["league_slug"], "bowl-fantasy")
            self.assertEqual(loaded["snapshots"]["k"]["value"], 1)
            self.assertTrue(clear_live_record_state("bowl-fantasy", instance_root=root))
            self.assertIsNone(load_live_record_state("bowl-fantasy", instance_root=root))


class NotifyWritesSidecarTest(unittest.TestCase):
    def test_sidecar_written_when_local_enqueue_skips(self) -> None:
        recorded: list[tuple[str, list]] = []

        def _capture(slug, events, instance_root=None):
            recorded.append((slug, list(events)))
            return len(events)

        key = "season:league:rs:goals"
        previous = {key: _holder(key=key, value=50)}
        current = {key: _holder(key=key, value=55, entity="player:2")}
        with patch(
            "app.services.record_broken_discord._load_snapshot_map",
            return_value=previous,
        ), patch(
            "app.services.record_broken_discord.collect_current_record_holders",
            return_value=current,
        ), patch(
            "app.services.record_broken_discord._upsert_snapshot",
        ), patch(
            "app.services.record_broken_discord.enqueue_record_broken_event",
            return_value=False,
        ), patch(
            "app.services.deploy_discord_records.record_deploy_record_break_events",
            _capture,
        ):
            stats = notify_record_breaks_after_import(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-cap",
            )
        self.assertEqual(stats["queued"], 0)
        self.assertEqual(stats["sidecar"], 1)
        self.assertEqual(recorded[0][0], "bowl-cap")
        self.assertEqual(recorded[0][1][0]["source_id"], "season:league:rs:goals:player:2:55")


class LiveStateDiffTest(unittest.TestCase):
    def test_snapshot_diff_builds_events(self) -> None:
        key = "season:league:rs:goals"
        live = {
            "snapshots": {key: _holder(key=key, value=50).to_json_dict()},
            "game_baselines": {},
        }
        current = {key: _holder(key=key, value=61, entity="player:9")}
        with patch(
            "app.services.record_broken_discord.collect_current_record_holders",
            return_value=current,
        ), patch(
            "app.services.record_broken_discord.collect_live_record_state",
            return_value={"game_baselines": {}},
        ):
            events = events_from_live_record_state_diff(
                MagicMock(),
                league_slug="bowl-historical",
                live_state=live,
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_id"], "season:league:rs:goals:player:9:61")
        self.assertIn("record_path", events[0]["payload"])

    def test_game_baseline_strict_beat(self) -> None:
        key = "game:rs:all:skater:goals"
        live = {
            "snapshots": {},
            "game_baselines": {
                key: {
                    "snapshot_key": key,
                    "metric_key": "goals",
                    "metric_title": "Goals",
                    "player_kind": "skater",
                    "segment": "rs",
                    "scope": "all",
                    "value": 5.0,
                    "display_value": "5",
                    "player_name": "Old",
                    "team_abbrev": "MTL",
                    "opponent_abbrev": "TOR",
                    "season_label": "1981-82",
                    "higher_is_better": True,
                }
            },
        }
        fresh = {
            "game_baselines": {
                key: {
                    "snapshot_key": key,
                    "metric_key": "goals",
                    "metric_title": "Goals",
                    "player_kind": "skater",
                    "segment": "rs",
                    "scope": "all",
                    "value": 6.0,
                    "display_value": "6",
                    "player_name": "New",
                    "team_abbrev": "EDM",
                    "opponent_abbrev": "CGY",
                    "season_label": "2025-26",
                    "higher_is_better": True,
                }
            }
        }
        with patch(
            "app.services.record_broken_discord.collect_current_record_holders",
            return_value={},
        ), patch(
            "app.services.record_broken_discord.collect_live_record_state",
            return_value=fresh,
        ):
            events = events_from_live_record_state_diff(
                MagicMock(),
                league_slug="bowl-cap",
                live_state=live,
            )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["payload"]["record_category"], "game")
        self.assertIn("New", events[0]["payload"]["new_record_line"])
        self.assertIn("Old", events[0]["payload"]["old_record_line"])


if __name__ == "__main__":
    unittest.main()
