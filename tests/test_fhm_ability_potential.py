"""FHM ability/potential grade parsing."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.routes import main
from app.services.player_ability_potential import (
    ability_potential_from_ratings_row,
    backfill_missing_ability_potential_from_ratings,
)
from app.services import prospect_system_rankings as psr
from scripts.import_pipeline.fhm_loader import _fhm_ability_potential_float


class FhmAbilityPotentialTests(unittest.TestCase):
    def test_import_parser_accepts_scouting_suffixes(self) -> None:
        self.assertEqual(_fhm_ability_potential_float("2Aa"), 2.0)
        self.assertEqual(_fhm_ability_potential_float("2.5Bc"), 2.5)

    def test_shared_parser_accepts_scouting_suffixes(self) -> None:
        self.assertEqual(
            ability_potential_from_ratings_row({"ability": "2Aa", "potential": "2.5Bc"}),
            (2.0, 2.5),
        )

    def test_backfill_missing_database_values_from_ratings_csv(self) -> None:
        player = SimpleNamespace(fhm_player_id=123, overall_ability=None, overall_potential=None)
        session = MagicMock()
        session.scalars.return_value.all.return_value = [player]

        with patch(
            "app.services.player_ability_potential.get_player_ratings_row",
            return_value={"ability": "2Aa", "potential": "2.5Bc"},
        ):
            changed = backfill_missing_ability_potential_from_ratings(session)

        self.assertEqual(changed, 1)
        self.assertEqual(player.overall_ability, 2.0)
        self.assertEqual(player.overall_potential, 2.5)
        session.commit.assert_called_once()

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
