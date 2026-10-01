"""Manual AP ledger tier grouping for BOWL-Relegation."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services.ap_service import ap_ledger_balance_sections
from app.services.relegation import RelegationTierConfig


class ApLedgerBalanceSectionTests(unittest.TestCase):
    def test_single_section_for_non_relegation(self) -> None:
        session = MagicMock()
        rows = [
            {"team": SimpleNamespace(id=1, name="Alpha"), "balance": 10},
            {"team": SimpleNamespace(id=2, name="Beta"), "balance": 5},
        ]
        sections = ap_ledger_balance_sections(session, "bowl-cap", rows)
        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].key, "all")
        self.assertEqual(sections[0].total_balance, 15)
        self.assertEqual(len(sections[0].rows), 2)

    def test_blup_blow_other_sections_when_split_active(self) -> None:
        session = MagicMock()
        cfg = RelegationTierConfig(
            mode="league_id",
            upper_league_ids=frozenset({0}),
            lower_league_ids=frozenset({1}),
            upper_conference_ids=frozenset(),
            lower_conference_ids=frozenset(),
            upper_label="BOWL-Upper",
            lower_label="BOWL-Lower",
            combined_league_ids=(0, 1),
        )
        rows = [
            {
                "team": SimpleNamespace(id=1, name="Upper", fhm_league_id=0, fhm_conference_id=None),
                "balance": 3,
            },
            {
                "team": SimpleNamespace(id=2, name="Lower", fhm_league_id=1, fhm_conference_id=None),
                "balance": 7,
            },
            {
                "team": SimpleNamespace(id=3, name="Farm", fhm_league_id=99, fhm_conference_id=None),
                "balance": 1,
            },
        ]
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["RELEGATION_SPLIT_ACTIVE"] = True
        with app.app_context():
            with patch("app.services.relegation.get_tier_config", return_value=cfg):
                sections = ap_ledger_balance_sections(session, "bowl-fantasy", rows)
        self.assertEqual([s.key for s in sections], ["upper", "lower", "other"])
        self.assertEqual(sections[0].total_balance, 3)
        self.assertEqual(sections[1].total_balance, 7)
        self.assertEqual(sections[2].total_balance, 1)


if __name__ == "__main__":
    unittest.main()
