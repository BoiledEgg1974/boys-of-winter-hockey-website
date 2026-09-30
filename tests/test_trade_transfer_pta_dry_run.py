"""Trade Tool PTA dry-run (BOWL-Relegation)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.trade_transfer_pta import (
    parse_trade_ledger_object,
    player_ids_from_drag_keys,
    relegation_trade_pta_dry_run_only,
    run_trade_pta_dry_run,
)


class TradeTransferPtaDryRunTests(unittest.TestCase):
    def test_dry_run_flag_default_on_fantasy(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            self.assertTrue(relegation_trade_pta_dry_run_only("bowl-fantasy"))
        self.assertFalse(relegation_trade_pta_dry_run_only("bowl-cap"))

    def test_parse_ledger_with_compensation(self) -> None:
        raw = (
            '{"from_left_to_right":["player:1"],'
            '"from_right_to_left":["player:2"],'
            '"transfer_compensation":{"left_acquiring":{"pta_transfer_fee":350000,"cash_sweetener":0},'
            '"right_acquiring":{"pta_transfer_fee":0,"cash_sweetener":50000}}}'
        )
        obj = parse_trade_ledger_object(raw)
        self.assertEqual(obj["from_left_to_right"], ["player:1"])
        self.assertEqual(obj["transfer_compensation"]["left_acquiring"]["pta_transfer_fee"], 350000)

    def test_player_ids_from_drag_keys(self) -> None:
        self.assertEqual(player_ids_from_drag_keys(["player:9", "pick:1", "player:3"]), [9, 3])

    def test_run_rejects_non_relegation(self) -> None:
        out = run_trade_pta_dry_run(
            MagicMock(),
            league_slug="bowl-cap",
            left_team_id=1,
            right_team_id=2,
            ledger_raw='{"from_left_to_right":[],"from_right_to_left":[]}',
            raw_dir=None,
        )
        self.assertFalse(out["valid"])


if __name__ == "__main__":
    unittest.main()
