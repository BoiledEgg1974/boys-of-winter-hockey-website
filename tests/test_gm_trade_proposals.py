"""GM trade proposal helpers."""
from __future__ import annotations

import unittest

from app.services.trade_transfer_pta import gm_trade_proposals_enabled


class GmTradeProposalsFlagTests(unittest.TestCase):
    def test_enabled_on_relegation_by_default(self) -> None:
        self.assertTrue(gm_trade_proposals_enabled("bowl-fantasy"))

    def test_disabled_on_cap(self) -> None:
        self.assertFalse(gm_trade_proposals_enabled("bowl-cap"))


if __name__ == "__main__":
    unittest.main()
