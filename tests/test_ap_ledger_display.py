"""AP ledger display helpers for GM/admin pages."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.ap_service import (
    LEDGER_KIND_EARNED,
    LEDGER_KIND_PENALIZED,
    LEDGER_KIND_REDEEMED,
    ledger_entry_description,
    ledger_entry_kind,
    parse_ledger_list_params,
)


class ApLedgerDisplayTests(unittest.TestCase):
    def test_ledger_entry_kind(self) -> None:
        self.assertEqual(ledger_entry_kind(3, "news_article"), LEDGER_KIND_EARNED)
        self.assertEqual(ledger_entry_kind(-2, "batch_penalties"), LEDGER_KIND_PENALIZED)
        self.assertEqual(ledger_entry_kind(-5, "redemption"), LEDGER_KIND_REDEEMED)

    def test_ledger_entry_description_prefers_note(self) -> None:
        desc = ledger_entry_description("manual", {"note": "EXPORT: +1 AP (BOWL-Cap)"})
        self.assertEqual(desc, "EXPORT: +1 AP (BOWL-Cap)")

    def test_ledger_entry_description_batch_label(self) -> None:
        desc = ledger_entry_description("batch_all_star", {"batch": "ALL-STAR"})
        self.assertEqual(desc, "ALL-STAR")

    def test_ledger_entry_description_redemption_lines(self) -> None:
        desc = ledger_entry_description(
            "redemption",
            {
                "request_id": 12,
                "lines": [{"title": "Extra roster spot", "cost": 4}],
            },
        )
        self.assertEqual(desc, "Extra roster spot")

    def test_parse_ledger_list_params_defaults(self) -> None:
        raw = MagicMock()
        raw.get.side_effect = lambda k, default=None: {
            "ledger_page": None,
            "ledger_team": None,
            "ledger_kind": None,
        }.get(k, default)
        page, team_id, kind = parse_ledger_list_params(raw)
        self.assertEqual((page, team_id, kind), (1, None, None))

    def test_parse_ledger_list_params_filters(self) -> None:
        raw = MagicMock()
        raw.get.side_effect = lambda k, default=None: {
            "ledger_page": "3",
            "ledger_team": "7",
            "ledger_kind": "earned",
        }.get(k, default)
        page, team_id, kind = parse_ledger_list_params(raw)
        self.assertEqual((page, team_id, kind), (3, 7, LEDGER_KIND_EARNED))

    def test_parse_ledger_list_params_locked_team(self) -> None:
        raw = MagicMock()
        raw.get.side_effect = lambda k, default=None: {
            "ledger_page": "2",
            "ledger_team": "99",
            "ledger_kind": "redeemed",
        }.get(k, default)
        page, team_id, kind = parse_ledger_list_params(raw, locked_team_id=12)
        self.assertEqual((page, team_id, kind), (2, 12, LEDGER_KIND_REDEEMED))


if __name__ == "__main__":
    unittest.main()
