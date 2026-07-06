import unittest
from types import SimpleNamespace

from app.services.depth_chart_sort import depth_chart_player_sort_key


class DepthChartSortTests(unittest.TestCase):
    def test_sort_key_prefers_ovr_over_position_rating(self) -> None:
        """Depth chart ranks by composite OVR badge, not positional attribute ratings."""
        high_ovr = SimpleNamespace(
            full_name="High Ovr",
            overall_ability=4.5,
            overall_potential=4.5,
            fhm_player_id=None,
        )
        high_lw_only = SimpleNamespace(
            full_name="High Lw Rating",
            overall_ability=3.0,
            overall_potential=3.0,
            fhm_player_id=None,
        )
        ratings = {
            "lw": 20,
            "c": 10,
            "rw": 10,
            "ability": 3.0,
            "potential": 3.0,
            "skating": 10,
            "shooting": 10,
            "playmaking": 10,
            "defending": 10,
            "physicality": 10,
            "conditioning": 10,
            "character": 10,
            "hockey_sense": 10,
        }
        self.assertGreater(
            depth_chart_player_sort_key(high_ovr, bucket="LW"),
            depth_chart_player_sort_key(
                high_lw_only, bucket="LW", ratings_row=ratings
            ),
        )

    def test_sort_key_uses_csv_abi_when_db_field_missing(self) -> None:
        missing_db = SimpleNamespace(
            full_name="Csv Abi",
            overall_ability=None,
            overall_potential=None,
            fhm_player_id="999",
        )
        key = depth_chart_player_sort_key(
            missing_db,
            bucket="C",
            ratings_row={"ability": "3.5Aa", "potential": "4.0Bc", "c": 12},
        )
        self.assertGreaterEqual(key[1], 3.5)

    def test_merge_order_is_descending_by_ovr(self) -> None:
        players = [
            SimpleNamespace(
                full_name="B",
                overall_ability=3.0,
                overall_potential=3.0,
                fhm_player_id=None,
            ),
            SimpleNamespace(
                full_name="A",
                overall_ability=4.5,
                overall_potential=4.5,
                fhm_player_id=None,
            ),
            SimpleNamespace(
                full_name="C",
                overall_ability=2.5,
                overall_potential=2.5,
                fhm_player_id=None,
            ),
        ]
        ordered = sorted(
            players,
            key=lambda p: depth_chart_player_sort_key(p, bucket="RW"),
            reverse=True,
        )
        self.assertEqual([p.full_name for p in ordered], ["A", "B", "C"])


if __name__ == "__main__":
    unittest.main()
