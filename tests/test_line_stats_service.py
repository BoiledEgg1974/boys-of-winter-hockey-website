"""Line stats service unit tests."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.line_stats import (
    LINE_TYPE_DEFENSE,
    LINE_TYPE_FORWARD,
    _build_line_row,
    _line_specs,
    _player_process_snapshot,
    _shot_share_proxy,
    aggregate_line_metrics,
)


class _FakeStat:
    def __init__(self, **kwargs) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class LineStatsServiceTest(unittest.TestCase):
    def test_forward_line_weighted_aggregation(self) -> None:
        snapshots = [
            {
                "gp": 20,
                "toi_seconds": 1200 * 60,
                "cf_pct": 52.0,
                "ff_pct": 51.0,
                "sf_per_60": 30.0,
                "pts_per_60": 2.0,
                "pdo": 101.0,
                "gf_per_60": 3.0,
                "ga_per_60": 2.5,
                "shot_share_proxy": 52.0,
            },
            {
                "gp": 18,
                "toi_seconds": 900 * 60,
                "cf_pct": 48.0,
                "ff_pct": 49.0,
                "sf_per_60": 28.0,
                "pts_per_60": 1.5,
                "pdo": 99.0,
                "gf_per_60": 2.5,
                "ga_per_60": 2.8,
                "shot_share_proxy": 48.0,
            },
            {
                "gp": 22,
                "toi_seconds": 1100 * 60,
                "cf_pct": 50.0,
                "ff_pct": 50.0,
                "sf_per_60": 32.0,
                "pts_per_60": 2.2,
                "pdo": 100.0,
                "gf_per_60": 2.8,
                "ga_per_60": 2.6,
                "shot_share_proxy": 50.0,
            },
        ]
        out = aggregate_line_metrics(snapshots)
        self.assertEqual(out["combined_gp"], 60)
        self.assertEqual(out["combined_toi_seconds"], 3200 * 60)
        self.assertIsNotNone(out["avg_cf_pct"])
        self.assertIsNotNone(out["avg_ff_pct"])
        self.assertIsNotNone(out["avg_sf_per_60"])
        self.assertIsNotNone(out["avg_pts_per_60"])
        self.assertIsNotNone(out["avg_pdo"])
        self.assertGreater(out["avg_cf_pct"], 48.0)
        self.assertLess(out["avg_cf_pct"], 52.0)
        self.assertFalse(out["missing_stats"])

    def test_defense_pair_simple_average_without_toi(self) -> None:
        snapshots = [
            {
                "gp": 10,
                "toi_seconds": 0,
                "cf_pct": 54.0,
                "ff_pct": 53.0,
                "sf_per_60": 5.0,
                "pts_per_60": 0.4,
                "pdo": 102.0,
                "gf_per_60": 2.1,
                "ga_per_60": 1.9,
                "shot_share_proxy": 55.0,
            },
            {
                "gp": 12,
                "toi_seconds": 0,
                "cf_pct": 46.0,
                "ff_pct": 47.0,
                "sf_per_60": 4.0,
                "pts_per_60": 0.2,
                "pdo": 98.0,
                "gf_per_60": 1.8,
                "ga_per_60": 2.4,
                "shot_share_proxy": 45.0,
            },
        ]
        out = aggregate_line_metrics(snapshots)
        self.assertEqual(out["avg_cf_pct"], 50.0)
        self.assertEqual(out["avg_ff_pct"], 50.0)
        self.assertEqual(out["avg_sf_per_60"], 4.5)
        self.assertEqual(out["avg_pdo"], 100.0)

    def test_missing_data_fallback(self) -> None:
        stat = _FakeStat(
            gp=15,
            toi_seconds=800 * 60,
            cf=None,
            ca=None,
            cf_pct=None,
            ff=None,
            fa=None,
            ff_pct=None,
            sf_per_60=None,
            sa_per_60=None,
            pdo=None,
            gf_per_60=None,
            ga_per_60=None,
            points=10,
        )
        snap = _player_process_snapshot(stat)
        self.assertIsNone(snap["cf_pct"])
        self.assertIsNone(snap["sf_per_60"])
        self.assertEqual(snap["pts_per_60"], 0.75)
        out = aggregate_line_metrics([snap, _player_process_snapshot(None)])
        self.assertTrue(out["missing_stats"])
        self.assertEqual(out["avg_pts_per_60"], 0.75)

    def test_shot_share_proxy(self) -> None:
        self.assertEqual(_shot_share_proxy(30.0, 28.0), 51.7)
        self.assertIsNone(_shot_share_proxy(None, 20.0))

    def test_line_specs(self) -> None:
        forward_specs = _line_specs(LINE_TYPE_FORWARD)
        defense_specs = _line_specs(LINE_TYPE_DEFENSE)
        self.assertEqual(len(forward_specs), 4)
        self.assertEqual(len(defense_specs), 4)
        self.assertEqual(forward_specs[0][0], "Forward")
        self.assertEqual(defense_specs[0][0], "Defense Pair")
        self.assertIn("es_l1_lw", forward_specs[0][2])

    def test_build_forward_line_row(self) -> None:
        team = SimpleNamespace(id=1, name="Testers", abbreviation="TST", slug="testers", fhm_team_id=3)
        players_by_fhm = {
            "101": SimpleNamespace(id=11, full_name="Left Wing", fhm_player_id="101"),
            "102": SimpleNamespace(id=12, full_name="Center Man", fhm_player_id="102"),
            "103": SimpleNamespace(id=13, full_name="Right Wing", fhm_player_id="103"),
        }
        stats_by_player_id = {
            11: _FakeStat(
                gp=20,
                toi_seconds=1000 * 60,
                cf=100,
                ca=90,
                cf_pct=52.6,
                ff=80,
                fa=75,
                ff_pct=51.6,
                sf_per_60=30.0,
                sa_per_60=28.0,
                pdo=101.0,
                gf_per_60=3.0,
                ga_per_60=2.5,
                points=40,
            ),
            12: _FakeStat(
                gp=20,
                toi_seconds=1000 * 60,
                cf=95,
                ca=95,
                cf_pct=50.0,
                ff=78,
                fa=78,
                ff_pct=50.0,
                sf_per_60=28.0,
                sa_per_60=28.0,
                pdo=100.0,
                gf_per_60=2.8,
                ga_per_60=2.6,
                points=35,
            ),
            13: _FakeStat(
                gp=20,
                toi_seconds=1000 * 60,
                cf=90,
                ca=100,
                cf_pct=47.4,
                ff=70,
                fa=80,
                ff_pct=46.7,
                sf_per_60=26.0,
                sa_per_60=30.0,
                pdo=99.0,
                gf_per_60=2.5,
                ga_per_60=2.7,
                points=30,
            ),
        }
        row = _build_line_row(
            team=team,
            line_type_label="Forward",
            unit="ES L1",
            slot_keys=("es_l1_lw", "es_l1_c", "es_l1_rw"),
            assignments={
                "es_l1_lw": "101",
                "es_l1_c": "102",
                "es_l1_rw": "103",
            },
            players_by_fhm=players_by_fhm,
            stats_by_player_id=stats_by_player_id,
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["line_type"], "Forward")
        self.assertEqual(row["unit"], "ES L1")
        self.assertEqual(row["player_count"], 3)
        self.assertIn("Left Wing", row["players_label"])
        self.assertAlmostEqual(row["avg_cf_pct"], 50.0, places=1)

    def test_build_line_row_requires_all_slots(self) -> None:
        team = SimpleNamespace(id=1, name="Testers", abbreviation="TST", slug="testers", fhm_team_id=3)
        players_by_fhm = {
            "201": SimpleNamespace(id=21, full_name="Left D", fhm_player_id="201"),
            "202": SimpleNamespace(id=22, full_name="Right D", fhm_player_id="202"),
        }
        row = _build_line_row(
            team=team,
            line_type_label="Defense Pair",
            unit="ES L2",
            slot_keys=("es_l2_ld", "es_l2_rd"),
            assignments={"es_l2_ld": "201"},
            players_by_fhm=players_by_fhm,
            stats_by_player_id={},
        )
        self.assertIsNone(row)


if __name__ == "__main__":
    unittest.main()
