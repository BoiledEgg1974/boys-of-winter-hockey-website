"""Tests for player development panel and rating snapshots."""
from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import player_development as pd
from app.services import player_rating_snapshots as prs


class ExtractRatingsTests(unittest.TestCase):
    def test_skater_keys_grouped(self) -> None:
        row = {k: 15.0 for k in prs.OFF_KEYS + prs.DEF_KEYS + prs.MENTAL_KEYS_SKATER + prs.PHYS_KEYS}
        out = prs.extract_ratings_dict(row, is_goalie=False)
        self.assertEqual(set(out.keys()), set(prs.SKATER_SNAPSHOT_KEYS))

    def test_goalie_crease_and_athletic_keys(self) -> None:
        row = {k: 12.0 for k in prs.GOALIE_CREASE_KEYS + prs.GOALIE_ATHLETIC_KEYS + prs.GOALIE_PUCK_KEYS}
        row.update({k: 11.0 for k in prs.MENTAL_KEYS_GOALIE})
        out = prs.extract_ratings_dict(row, is_goalie=True)
        self.assertIn("goalie_technique", out)
        self.assertIn("g_positioning", out)


class ChartAndSummaryTests(unittest.TestCase):
    def test_single_snapshot_empty_trend(self) -> None:
        snap = SimpleNamespace(
            snapshot_at=datetime.now(UTC),
            ratings_json=json.dumps({"passing": 14.0}),
            overall_score=70,
            ability=3.0,
            potential=3.5,
        )
        bundle = pd._chart_bundle([{"label": "May", "y": 14.0}])
        self.assertFalse(bundle["has_trend"])
        self.assertEqual(bundle["status_label"], "Current only")
        self.assertIn("next import", bundle["message"])

    def test_chart_status_labels_progression_and_regression(self) -> None:
        up = pd._chart_bundle([{"label": "Jan", "y": 10.0}, {"label": "May", "y": 12.0}])
        down = pd._chart_bundle([{"label": "Jan", "y": 14.0}, {"label": "May", "y": 13.0}])
        flat = pd._chart_bundle([{"label": "Jan", "y": 14.0}, {"label": "May", "y": 14.2}])
        self.assertEqual(up["status_kind"], "up")
        self.assertIn("Progressing", up["status_label"])
        self.assertEqual(down["status_kind"], "down")
        self.assertIn("Regressing", down["status_label"])
        self.assertEqual(flat["status_kind"], "flat")

    def test_summary_two_snapshots_counts_changes(self) -> None:
        t0 = datetime.now(UTC) - timedelta(days=120)
        t1 = datetime.now(UTC)
        s0 = SimpleNamespace(
            snapshot_at=t0,
            ratings_json=json.dumps({"passing": 12.0, "shooting_accuracy": 13.0}),
            overall_score=68,
            ability=2.5,
            potential=3.0,
        )
        s1 = SimpleNamespace(
            snapshot_at=t1,
            ratings_json=json.dumps({"passing": 14.5, "shooting_accuracy": 12.0}),
            overall_score=72,
            ability=2.8,
            potential=3.1,
        )
        summary = pd._summary_changes([s0, s1], ("passing", "shooting_accuracy"), current_row=None)
        self.assertTrue(summary["has_trend"])
        self.assertEqual(summary["attrs_changed"], 2)
        self.assertEqual(summary["improved_count"], 1)
        self.assertEqual(summary["regressed_count"], 1)
        self.assertEqual(summary["overall_delta"], 4)
        self.assertEqual(len(summary["risers"]), 1)
        self.assertEqual(len(summary["fallers"]), 1)


class BuildPanelTests(unittest.TestCase):
    def test_disabled_without_ratings(self) -> None:
        player = SimpleNamespace(id=1, position="C", retired=False)
        session = MagicMock()
        out = pd.build_player_development_panel(session, player, ratings_row=None, is_goalie=False)
        self.assertFalse(out["enabled"])

    @patch("app.services.player_development.seed_player_rating_snapshot_if_needed")
    @patch("app.services.player_development.load_player_rating_snapshots")
    def test_enabled_skater_tabs(self, load_snaps: MagicMock, seed: MagicMock) -> None:
        load_snaps.return_value = []
        seed.return_value = None
        player = SimpleNamespace(id=9, position="C", retired=False)
        row = {k: 14.0 for k in prs.OFF_KEYS}
        session = MagicMock()
        with patch("app.services.player_development.has_app_context", return_value=False):
            out = pd.build_player_development_panel(
                session,
                player,
                ratings_row=row,
                is_goalie=False,
                league_slug="bowl-fantasy",
            )
        self.assertTrue(out["enabled"])
        self.assertFalse(out["is_goalie"])
        self.assertEqual(len(out["tabs"]), 5)
        self.assertEqual(out["tabs"][0]["id"], "summary")
        self.assertEqual(out["tabs"][1]["id"], "offense")


if __name__ == "__main__":
    unittest.main()
