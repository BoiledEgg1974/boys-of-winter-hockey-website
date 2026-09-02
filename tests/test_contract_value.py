"""Peer-market contract value helpers for the team Finances tab."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.services.contract_value import (
    compact_money,
    english_ordinal,
    grade_tone,
    peer_median_aav,
    value_band,
    value_grade,
)


class ContractValueHelpersTest(unittest.TestCase):
    def test_compact_money_millions_and_thousands(self) -> None:
        self.assertEqual(compact_money(7_700_000), "$7.7M")
        self.assertEqual(compact_money(-62_400_000), "-$62.4M")
        self.assertEqual(compact_money(320_500), "$320.5k")
        self.assertEqual(compact_money(None), "—")

    def test_signed_compact_money(self) -> None:
        from app.services.contract_value import signed_compact_money

        self.assertEqual(signed_compact_money(500_000), "+$500k")
        self.assertEqual(signed_compact_money(-2_200_000), "-$2.2M")

    def test_value_grade_and_band(self) -> None:
        self.assertEqual(value_grade(150), "A+")
        self.assertEqual(value_grade(100), "B")
        self.assertEqual(value_grade(70), "C-")
        self.assertEqual(value_grade(40), "D-")
        self.assertEqual(value_band(120), "bargain")
        self.assertEqual(value_band(100), "fair")
        self.assertEqual(value_band(70), "overpay")
        self.assertEqual(grade_tone("A-"), "good")
        self.assertEqual(grade_tone("C"), "mid")
        self.assertEqual(grade_tone("D-"), "bad")

    def test_peer_median_uses_nearby_overalls(self) -> None:
        samples = [(70, 1_000_000), (71, 1_200_000), (72, 1_400_000), (73, 1_100_000), (90, 8_000_000)]
        med, n = peer_median_aav(72, samples, window=2, min_peers=3)
        self.assertGreaterEqual(n, 3)
        self.assertEqual(med, 1_150_000)

    def test_years_remaining_from_row(self) -> None:
        from app.services.contract_value import years_remaining_from_row

        row = {"major_2026": "1000000", "major_2027": "1000000", "major_2028": "-1"}
        self.assertEqual(years_remaining_from_row(row, 2026), 2)
        self.assertEqual(years_remaining_from_row(row, 2028), 0)
        self.assertEqual(years_remaining_from_row(None, 2026), 0)

    def test_english_ordinal(self) -> None:
        self.assertEqual(english_ordinal(1), "1st")
        self.assertEqual(english_ordinal(2), "2nd")
        self.assertEqual(english_ordinal(3), "3rd")
        self.assertEqual(english_ordinal(11), "11th")
        self.assertEqual(english_ordinal(32), "32nd")


    def test_bowl_cap_uses_cap_even_if_rule_false(self) -> None:
        from unittest.mock import MagicMock, patch

        from app.services.contract_value import league_uses_salary_cap

        session = MagicMock()
        with patch("app.services.contract_value.rule_bool", return_value=False):
            self.assertTrue(league_uses_salary_cap(session, "bowl-cap"))
            self.assertFalse(league_uses_salary_cap(session, "bowl-historical"))
            self.assertFalse(league_uses_salary_cap(session, "bowl-fantasy"))
    def test_team_page_has_finances_tab(self) -> None:
        root = Path(__file__).resolve().parents[1]
        team = (root / "app" / "templates" / "team.html").read_text(encoding="utf-8")
        partial = (root / "app" / "templates" / "_team_finances.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        main = (root / "app" / "routes" / "main.py").read_text(encoding="utf-8")
        self.assertIn("panel='finances'", team.replace('"', "'"))
        self.assertIn("active_panel == 'finances'", team)
        self.assertIn("_team_finances.html", team)
        self.assertIn("team-finances", partial)
        self.assertIn("data-team-finances", partial)
        self.assertIn(".team-finances", css)
        self.assertIn("--value-good", css)
        self.assertIn("initTeamFinancesPanel", js)
        self.assertIn('"finances"', main)
        self.assertIn("build_team_finances_payload", main)
        self.assertIn("Cap Efficiency", partial)
        self.assertIn("Contract Value", partial)
