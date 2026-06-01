"""Trade Market service validation and sorting."""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.services.trade_market import (
    BUYING_CATEGORY_KEYS,
    replace_buying_needs,
    sort_selling_rows,
    _validate_owned_asset,
)
from app.services.trade_tool import validate_ledger


class TradeMarketServiceTest(unittest.TestCase):
    def test_sort_selling_by_ovr_desc(self) -> None:
        rows = [
            {"asset_label": "Low", "ovr": 70},
            {"asset_label": "High", "ovr": 92},
            {"asset_label": "Mid", "ovr": 80},
        ]
        out = sort_selling_rows(rows, sort_key="ovr", order="desc")
        self.assertEqual([r["asset_label"] for r in out], ["High", "Mid", "Low"])

    def test_sort_selling_by_team_asc(self) -> None:
        rows = [
            {"team_name": "Toronto"},
            {"team_name": "Anaheim"},
        ]
        out = sort_selling_rows(rows, sort_key="team", order="asc")
        self.assertEqual(out[0]["team_name"], "Anaheim")

    def test_replace_buying_dedupes_categories(self) -> None:
        site = MagicMock()
        site.execute.return_value = None
        added = []

        def capture(row):
            added.append(row)

        site.add.side_effect = capture
        site.flush.return_value = None
        rows = replace_buying_needs(
            site,
            league_slug="bowl-cap",
            user_id=1,
            team_id=10,
            categories=["prospects", "prospects", "goalie", "invalid"],
            note="Need help",
        )
        self.assertEqual(len(rows), 2)
        cats = {r.category for r in rows}
        self.assertEqual(cats, {"prospects", "goalie"})
        self.assertTrue(cats <= BUYING_CATEGORY_KEYS)

    def test_validate_owned_asset_rejects_unknown_player(self) -> None:
        site = MagicMock()
        league = MagicMock()
        with patch(
            "app.services.trade_market.selectable_selling_assets",
            return_value={"roster": [], "unsigned": [], "draft_picks": []},
        ):
            with patch(
                "app.services.trade_market.draft_pick_owned_by_team",
                return_value=None,
            ):
                ok = _validate_owned_asset(
                    site,
                    league,
                    league_slug="bowl-cap",
                    team_id=1,
                    asset_type="contract",
                    asset_ref="player:999:roster",
                    raw_dir=None,
                )
        self.assertFalse(ok)

    def test_trade_tool_blocks_manual_picks_when_admin_ownership_exists(self) -> None:
        session = MagicMock()
        with patch(
            "app.services.trade_tool.draft_pick_ownership_exists",
            return_value=True,
        ):
            err = validate_ledger(
                session,
                from_team_id=1,
                to_team_id=2,
                left_out=["mpleft:1:testpick"],
                right_out=[],
                raw_dir=None,
                league_slug="bowl-cap",
                draft_round_cap=12,
            )
        self.assertEqual(
            err,
            "One or more assets leaving your team are not valid for your roster.",
        )


if __name__ == "__main__":
    unittest.main()
