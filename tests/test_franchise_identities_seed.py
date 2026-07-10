"""Franchise identity CSV seed helpers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.services.franchise_identities import (
    franchise_identities_need_csv_seed,
    norm_fhm_team_id,
)


class NormFhmTeamIdTest(unittest.TestCase):
    def test_preserves_zero(self) -> None:
        self.assertEqual(norm_fhm_team_id(0), "0")
        self.assertEqual(norm_fhm_team_id("0"), "0")

    def test_rejects_blank(self) -> None:
        self.assertIsNone(norm_fhm_team_id(None))
        self.assertIsNone(norm_fhm_team_id(""))
        self.assertIsNone(norm_fhm_team_id("   "))


class FranchiseIdentitySeedNeedTest(unittest.TestCase):
    def test_needs_seed_when_csv_has_more_rows_than_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "team_identity_history.csv"
            csv_path.write_text(
                "team_fhm_id,start_year,end_year,team_name,logo_file\n"
                "0,1917,1918,Montreal Canadiens,logo.png\n"
                "5,1924,1925,Boston Bruins,logo.png\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.scalar.return_value = 1
            self.assertTrue(franchise_identities_need_csv_seed(session, csv_path))

    def test_skips_when_counts_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "team_identity_history.csv"
            csv_path.write_text(
                "team_fhm_id,start_year,end_year,team_name,logo_file\n"
                "8,1926,1940,Chicago Black Hawks,logo.png\n",
                encoding="utf-8",
            )
            session = MagicMock()
            session.scalar.return_value = 1
            self.assertFalse(franchise_identities_need_csv_seed(session, csv_path))


if __name__ == "__main__":
    unittest.main()
