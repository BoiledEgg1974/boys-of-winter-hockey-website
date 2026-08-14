"""Unit tests for BOWL analytics percentile card service."""
from __future__ import annotations

import unittest

from app.models import PlayerGoalieStat, Team
from app.services.player_percentiles import (
    _GOALIE_GRID_KEYS,
    _SKATER_GRID_KEYS,
    _cap_label,
    _headline_dict,
    _team_accent_colors,
    _consistency_score,
    _display_pct,
    _empty_goalie_grid,
    _empty_skater_grid,
    _goalie_gp_pct,
    _goalie_war_pct_from_metrics,
    _pct_tier,
    _projected_war_pct,
    _start_quality_metrics,
    _war_pct_from_metrics,
    bowl_goalie_war_raw,
    bowl_war_raw,
    chart_svg,
    finishing_value,
    percentile_int,
)


class PlayerPercentilesTests(unittest.TestCase):
    def test_percentile_int_median(self) -> None:
        pool = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(percentile_int(3.0, pool), 50)

    def test_percentile_int_empty_pool(self) -> None:
        self.assertIsNone(percentile_int(3.0, []))

    def test_percentile_int_lower_is_better(self) -> None:
        pool = [2.0, 3.0, 4.0]
        self.assertEqual(percentile_int(2.0, pool, higher_is_better=False), 75)

    def test_percentile_int_never_returns_100(self) -> None:
        pool = [1.0, 2.0, 3.0, 4.0, 5.0]
        self.assertEqual(percentile_int(5.0, pool), 83)
        self.assertLessEqual(percentile_int(5.0, pool) or 0, 99)

    def test_bowl_war_raw_uses_component_percentiles(self) -> None:
        metrics = {
            "gf_per_60": 5.0,
            "ga_per_60": 1.0,
            "cf_pct_rel": 5.0,
            "game_rating_off": 5.0,
            "game_rating_def": 5.0,
            "pp_pts_per_60": 5.0,
            "sh_pts_per_60": 5.0,
            "finishing": 5.0,
        }
        pools = {k: [1.0, 2.0, 3.0, 4.0, 5.0] for k in metrics}
        val = bowl_war_raw(metrics, pools)
        self.assertIsNotNone(val)
        assert val is not None
        self.assertLess(val, 90.0)
        self.assertGreater(val, 60.0)

    def test_projected_war_pct_caps_at_99(self) -> None:
        self.assertEqual(
            _projected_war_pct(95, age=20, abi=70.0, pot=90.0, game_rating=60.0),
            99,
        )
        self.assertEqual(
            _projected_war_pct(100, age=20, abi=70.0, pot=90.0, game_rating=60.0),
            99,
        )

    def test_war_pct_from_metrics_caps_at_99(self) -> None:
        metrics = {k: 5.0 for k in (
            "gf_per_60", "ga_per_60", "cf_pct_rel", "game_rating_off",
            "game_rating_def", "pp_pts_per_60", "sh_pts_per_60", "finishing",
        )}
        pools = {k: [1.0, 2.0, 3.0, 4.0, 5.0] for k in metrics}
        self.assertLessEqual(_war_pct_from_metrics(metrics, pools) or 0, 99)

    def test_penalties_fewer_is_better(self) -> None:
        pool = [10.0, 20.0, 30.0, 40.0]
        self.assertGreater(
            percentile_int(10.0, pool, higher_is_better=False) or 0,
            percentile_int(40.0, pool, higher_is_better=False) or 0,
        )

    def test_goals_uses_count_not_rate(self) -> None:
        goals_key = next(row for row in _SKATER_GRID_KEYS if row[0] == "Goals")
        self.assertEqual(goals_key, ("Goals", "goals", True))
        penalties_key = next(row for row in _SKATER_GRID_KEYS if row[0] == "Penalties")
        self.assertEqual(penalties_key[1], "pim")
        self.assertFalse(penalties_key[2])

    def test_finishing_value(self) -> None:
        self.assertAlmostEqual(finishing_value(10, 100), 1.0)

    def test_bowl_war_raw_uses_available_components(self) -> None:
        metrics = {
            "gf_per_60": 3.0,
            "ga_per_60": 2.0,
            "cf_pct_rel": 1.5,
            "game_rating_off": 55.0,
            "game_rating_def": 50.0,
            "pp_pts_per_60": 4.0,
            "sh_pts_per_60": 1.0,
            "finishing": 2.0,
        }
        pools = {k: [1.0, 2.0, 3.0, 4.0, 5.0] for k in metrics}
        val = bowl_war_raw(metrics, pools)
        self.assertIsNotNone(val)
        assert val is not None
        self.assertGreater(val, 0.0)
        self.assertLessEqual(val, 99.0)

    def test_chart_svg_builds_path(self) -> None:
        out = chart_svg(
            ["23-24", "24-25"],
            [{"values": [40, 80], "class": "player-analytics-card__chart-line--war"}],
        )
        self.assertTrue(out["has_data"])
        self.assertEqual(len(out["paths"]), 1)
        self.assertIn("M ", out["paths"][0]["d"])
        self.assertEqual(len(out["paths"][0]["dots"]), 2)
        self.assertTrue(out["paths"][0]["dots"][0]["highlight"])
        self.assertEqual(len(out["y_labels"]), 5)
        self.assertEqual(len(out["grid_lines"]), 5)
        self.assertEqual(len(out["x_labels"]), 2)
        self.assertEqual(out["x_labels"][0]["anchor"], "start")
        self.assertEqual(out["x_labels"][-1]["anchor"], "end")
        self.assertEqual(out["width"], 360)
        mid = next(line for line in out["grid_lines"] if line["mid"])
        self.assertAlmostEqual(mid["y"], next(lbl["y"] for lbl in out["y_labels"] if lbl["text"] == "50%"))

    def test_chart_svg_thins_crowded_x_labels(self) -> None:
        labels = [f"{i:02d}/01" for i in range(1, 25)]
        out = chart_svg(
            labels,
            [{"values": list(range(10, 34)), "class": "player-analytics-card__chart-line--war"}],
        )
        self.assertLessEqual(len(out["x_labels"]), 7)
        self.assertEqual(out["x_labels"][0]["text"], "01/01")
        self.assertEqual(out["x_labels"][-1]["text"], "24/01")
        self.assertEqual(len(out["paths"][0]["dots"]), 2)
        self.assertEqual(len(out["x_ticks"]), len(out["x_labels"]))

    def test_chart_svg_requires_two_points(self) -> None:
        out = chart_svg(["24-25"], [{"values": [50], "class": "x"}])
        self.assertFalse(out["has_data"])

    def test_empty_skater_grid_uses_dashes(self) -> None:
        grid = _empty_skater_grid()
        self.assertEqual(len(grid), 9)
        self.assertTrue(all(cell["display"] == "—" for cell in grid))
        self.assertTrue(all(cell["tier"] == "empty" for cell in grid))

    def test_display_pct_and_tier_for_missing(self) -> None:
        self.assertEqual(_display_pct(None), "—")
        self.assertEqual(_pct_tier(None), "empty")

    def test_empty_goalie_grid_has_ten_percentile_cells(self) -> None:
        grid = _empty_goalie_grid()
        self.assertEqual(len(grid), 10)
        self.assertEqual(len(_GOALIE_GRID_KEYS), 10)
        self.assertTrue(all(cell["display"] == "—" for cell in grid))

    def test_start_quality_metrics_from_game_ratings(self) -> None:
        ratings = [55.0, 60.0, 65.0, 70.0, 75.0]
        out = _start_quality_metrics(ratings, median=60.0, p75=70.0, p25=55.0)
        self.assertEqual(out["quality_start_pct"], 80.0)
        self.assertEqual(out["excellent_start_pct"], 40.0)
        self.assertEqual(out["bad_start_pct"], 20.0)

    def test_start_quality_metrics_need_minimum_games(self) -> None:
        out = _start_quality_metrics([70.0, 80.0], median=60.0, p75=75.0, p25=50.0)
        self.assertIsNone(out["quality_start_pct"])

    def test_consistency_score_prefers_stable_starts(self) -> None:
        steady = _consistency_score([60.0, 61.0, 59.0, 60.0, 62.0])
        volatile = _consistency_score([40.0, 75.0, 50.0, 80.0, 45.0])
        assert steady is not None and volatile is not None
        self.assertGreater(steady, volatile)

    def test_goalie_gp_pct_from_starts(self) -> None:
        st = PlayerGoalieStat(gp=20, games_started=14)
        self.assertEqual(_goalie_gp_pct(st), 70)

    def test_cap_label_compact_uses_k_suffix(self) -> None:
        contract = type("C", (), {"average_salary": 266_100})()
        self.assertEqual(_cap_label(contract, 3, compact=True), "$266k x 3")
        self.assertEqual(_cap_label(contract, 3, compact=False), "$266,100 x 3")

    def test_team_accent_colors_from_team_palette(self) -> None:
        team = Team(primary_color="#C8102E", secondary_color="#000000")
        primary, secondary = _team_accent_colors(team)
        self.assertEqual(primary, "#C8102E")
        self.assertEqual(secondary, "#000000")

    def test_headline_includes_team_colors(self) -> None:
        player = type("P", (), {"full_name": "Test Player", "position": "G"})()
        team = Team(primary_color="#C8102E", secondary_color="#FFB81C")
        headline = _headline_dict(
            player,
            photo_url=None,
            team_logo_url=None,
            team=team,
            proj_war_pct=71,
            player_age=21,
            role_title="Starter",
            contract=None,
            years_left=None,
            is_goalie=True,
        )
        self.assertEqual(headline["team_primary_color"], "#C8102E")
        self.assertEqual(headline["team_secondary_color"], "#FFB81C")

    def test_bowl_goalie_war_raw_uses_component_percentiles(self) -> None:
        metrics = {
            "sv_pct": 0.92,
            "gsaa": 10.0,
            "gaa": 2.5,
            "game_rating": 75.0,
            "minutes": 2000.0,
            "quality_start_pct": 80.0,
            "excellent_start_pct": 50.0,
            "bad_start_pct": 20.0,
            "consistency": 85.0,
        }
        pools = {k: [1.0, 2.0, 3.0, 4.0, 5.0] for k in metrics}
        pools["sv_pct"] = [0.88, 0.89, 0.90, 0.91, 0.92]
        val = bowl_goalie_war_raw(metrics, pools)
        self.assertIsNotNone(val)
        assert val is not None
        self.assertLessEqual(val, 99.0)

    def test_goalie_sv_chart_supports_league_avg_series(self) -> None:
        out = chart_svg(
            ["22-23", "23-24"],
            [
                {"values": [91.2, 91.8], "class": "player-analytics-card__chart-line--war"},
                {
                    "values": [90.5, 90.9],
                    "class": "player-analytics-card__chart-line--fin",
                    "stroke_dasharray": "4 3",
                },
            ],
            ymin=89.0,
            ymax=93.0,
        )
        self.assertTrue(out["has_data"])
        self.assertEqual(len(out["paths"]), 2)
        self.assertEqual(out["y_labels"][0]["text"], "89")
        self.assertEqual(out["y_labels"][-1]["text"], "93")
        self.assertFalse(any(line["mid"] for line in out["grid_lines"]))

    def test_goalie_war_pct_from_metrics_caps_at_99(self) -> None:
        metrics = {
            "sv_pct": 0.95,
            "gsaa": 20.0,
            "gaa": 1.8,
            "game_rating": 90.0,
            "minutes": 2500.0,
            "quality_start_pct": 95.0,
            "excellent_start_pct": 80.0,
            "bad_start_pct": 10.0,
            "consistency": 95.0,
        }
        pools = {
            "sv_pct": [0.88, 0.90, 0.91, 0.92, 0.95],
            "gsaa": [1.0, 5.0, 10.0, 15.0, 20.0],
            "gaa": [3.5, 3.0, 2.5, 2.0, 1.8],
            "game_rating": [60.0, 70.0, 75.0, 80.0, 90.0],
            "minutes": [1000.0, 1500.0, 1800.0, 2000.0, 2500.0],
            "quality_start_pct": [50.0, 60.0, 70.0, 80.0, 95.0],
            "excellent_start_pct": [30.0, 40.0, 50.0, 60.0, 80.0],
            "bad_start_pct": [40.0, 30.0, 25.0, 15.0, 10.0],
            "consistency": [60.0, 70.0, 80.0, 85.0, 95.0],
        }
        self.assertLessEqual(_goalie_war_pct_from_metrics(metrics, pools) or 0, 99)


if __name__ == "__main__":
    unittest.main()
