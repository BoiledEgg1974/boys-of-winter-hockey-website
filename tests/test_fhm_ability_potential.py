"""FHM ability/potential grade parsing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.routes import main
from app.services import prospect_system_rankings as psr
from scripts.import_pipeline.fhm_loader import _fhm_ability_potential_float


class FhmAbilityPotentialTests(unittest.TestCase):
    def test_import_parser_accepts_scouting_suffixes(self) -> None:
        self.assertEqual(_fhm_ability_potential_float("2Aa"), 2.0)
        self.assertEqual(_fhm_ability_potential_float("2.5Bc"), 2.5)

    def test_prospect_pages_fall_back_to_ratings_csv_values(self) -> None:
        player = SimpleNamespace(overall_ability=None, overall_potential=None)
        row = {"ability": "2Aa", "potential": "2.5Bc"}

        self.assertEqual(main._player_abi_pot_value(player, row, "ability"), 2.0)
        self.assertEqual(main._player_abi_pot_value(player, row, "potential"), 2.5)

    def test_prospect_system_rankings_fall_back_to_ratings_csv_values(self) -> None:
        player = SimpleNamespace(overall_ability=None, overall_potential=None)
        row = {"ability": "2Aa", "potential": "2.5Bc"}

        self.assertEqual(psr._player_abi_pot_value(player, row, "ability"), 2.0)
        self.assertEqual(psr._player_abi_pot_value(player, row, "potential"), 2.5)


if __name__ == "__main__":
    unittest.main()
