"""Unit tests for BOWL prospect projection metrics."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.models import Player
from app.services.prospect_projection import (
    PROSPECT_PROJECTION_HEADERS,
    PROSPECT_PROJECTION_SORT_KEYS,
    build_prospect_projection,
    format_projection_pct,
    projection_sort_value,
)


class ProspectProjectionTests(unittest.TestCase):
    def _player(self, **kwargs) -> Player:
        defaults = {
            "first_name": "Test",
            "last_name": "Prospect",
            "full_name": "Test Prospect",
            "position": "C",
            "overall_ability": 3.0,
            "overall_potential": 4.5,
            "nationality": "Canada",
            "height_inches": 71,
            "weight_lbs": 185,
        }
        defaults.update(kwargs)
        return Player(**defaults)

    def test_format_projection_pct_caps_display(self) -> None:
        self.assertEqual(format_projection_pct(None), "—")
        self.assertEqual(format_projection_pct(72), "72%")
        self.assertEqual(format_projection_pct(99), ">99%")

    def test_higher_pot_produces_higher_star_and_bowl_pct(self) -> None:
        low = build_prospect_projection(
            self._player(overall_potential=2.5),
            abi=2.5,
            pot=2.5,
            ratings_row={"skating": 12, "shooting": 11, "playmaking": 10, "defending": 9,
                         "physicality": 10, "conditioning": 11, "character": 12, "hockey_sense": 11},
            age=18.0,
            league_slug="bowl-fantasy",
            season=None,
        )
        high = build_prospect_projection(
            self._player(overall_potential=4.8),
            abi=3.5,
            pot=4.8,
            ratings_row={"skating": 17, "shooting": 16, "playmaking": 16, "defending": 14,
                         "physicality": 15, "conditioning": 16, "character": 15, "hockey_sense": 16},
            age=18.0,
            league_slug="bowl-fantasy",
            season=None,
        )
        assert low["star_pct"] is not None and high["star_pct"] is not None
        assert low["bowl_pct"] is not None and high["bowl_pct"] is not None
        self.assertLess(low["star_pct"], high["star_pct"])
        self.assertLess(low["bowl_pct"], high["bowl_pct"])

    def test_bowle_increases_from_dy_minus_one_to_dy(self) -> None:
        out = build_prospect_projection(
            self._player(),
            abi=3.2,
            pot=4.2,
            ratings_row={"skating": 15, "shooting": 14, "playmaking": 14, "defending": 12,
                         "physicality": 13, "conditioning": 14, "character": 14, "hockey_sense": 14},
            age=18.5,
            league_slug="bowl-fantasy",
            season=None,
        )
        assert out["bowle_dy_m1"] is not None and out["bowle_dy"] is not None
        self.assertLess(out["bowle_dy_m1"], out["bowle_dy"])

    def test_missing_abi_pot_returns_dashes(self) -> None:
        out = build_prospect_projection(
            self._player(overall_ability=None, overall_potential=None),
            abi=None,
            pot=None,
            ratings_row=None,
            age=19.0,
            league_slug="bowl-fantasy",
            season=None,
        )
        self.assertIsNone(out["star_pct"])
        self.assertEqual(out["star_display"], "—")
        self.assertIsNone(out["popover"])

    def test_percentiles_cap_at_99(self) -> None:
        out = build_prospect_projection(
            self._player(overall_potential=5.0),
            abi=5.0,
            pot=5.0,
            ratings_row={"skating": 19, "shooting": 19, "playmaking": 19, "defending": 18,
                         "physicality": 18, "conditioning": 19, "character": 19, "hockey_sense": 19},
            age=17.5,
            league_slug="bowl-fantasy",
            season=None,
        )
        self.assertLessEqual(out["star_pct"] or 0, 99)
        self.assertLessEqual(out["bowl_pct"] or 0, 99)

    def test_projection_sort_value_keys(self) -> None:
        proj = {"star_pct": 80, "bowl_pct": 70, "bowle_dy_m1": 18.5, "bowle_dy": 21.0}
        self.assertEqual(projection_sort_value(proj, "star"), 80.0)
        self.assertEqual(projection_sort_value(proj, "bowle_dy"), 21.0)
        self.assertIsNone(projection_sort_value(None, "star"))

    def test_projection_headers_count(self) -> None:
        self.assertEqual(len(PROSPECT_PROJECTION_HEADERS), 4)
        self.assertEqual(len(PROSPECT_PROJECTION_SORT_KEYS), 4)

    def test_bowle_header_tooltips_explain_scale(self) -> None:
        tips = {sk: tip for sk, _, tip in PROSPECT_PROJECTION_HEADERS}
        for key in ("bowle_dy_m1", "bowle_dy"):
            self.assertIn("0", tips[key])
            self.assertIn("15", tips[key])
            self.assertIn("BOWLe", tips[key])
        self.assertIn("draft year minus one", tips["bowle_dy_m1"].lower())
        self.assertIn("draft eligibility", tips["bowle_dy"].lower())
        self.assertIn("DYe", tips["bowle_dy_m1"])
        labels = [abbr for _, abbr, _ in PROSPECT_PROJECTION_HEADERS]
        self.assertEqual(labels, ["BOWL Star", "BOWL Lg%", "DY-1e", "DYe"])

    def test_templates_include_projection_columns(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for name in ("prospects.html", "undrafted_prospects.html", "draft_eligible.html"):
            text = (root / "app" / "templates" / name).read_text(encoding="utf-8")
            self.assertIn("prospect_projection_headers_row", text)
            self.assertIn("prospect_projection_headers)", text)
            self.assertIn("prospect_projection_cells", text)
            self.assertIn("prospect_projection_footnote_block", text)


if __name__ == "__main__":
    unittest.main()
