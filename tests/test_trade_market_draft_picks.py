"""Draft-pick ownership CSV import (Trade Market / Trade Tool)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.services.draft_pick_ownership import (
    DRAFT_PICK_CSV_NAME,
    describe_draft_pick_row,
    draft_pick_drag_key,
    import_draft_pick_ownership_csv,
)


class DraftPickOwnershipCsvTest(unittest.TestCase):
    def test_import_parses_year_team_and_round_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            path = raw / DRAFT_PICK_CSV_NAME
            path.write_text(
                "Year;Team ID;1st Round;2nd Round;3rd Round;10th Round;11th Round\n"
                "2000;5;5;12;5;;12\n"
                "2001;12;12;5;12;;\n",
                encoding="utf-8",
            )
            site_session = MagicMock()
            league_session = MagicMock()
            fhm_map = {5: 101, 12: 102}
            teams = {
                101: MagicMock(abbreviation="STL", full_display_name=lambda: "St. Louis"),
                102: MagicMock(abbreviation="BOS", full_display_name=lambda: "Boston"),
            }

            def get_team(_session, tid):
                return teams.get(int(tid))

            league_session.get.side_effect = get_team
            league_session.scalars.return_value.all.return_value = [
                MagicMock(fhm_team_id="5", id=101),
                MagicMock(fhm_team_id="12", id=102),
            ]

            added: list = []

            def add(row):
                added.append(row)

            site_session.execute.return_value = None
            site_session.add.side_effect = add
            site_session.flush.return_value = None

            with unittest.mock.patch(
                "app.services.draft_pick_ownership.fhm_team_id_to_db_id",
                return_value=fhm_map,
            ):
                n = import_draft_pick_ownership_csv(
                    site_session,
                    league_session,
                    league_slug="bowl-test",
                    raw_dir=raw,
                )
            self.assertEqual(n, 7)
            self.assertEqual(len(added), 7)
            first = added[0]
            self.assertEqual(first.league_slug, "bowl-test")
            self.assertEqual(first.draft_year, 2000)
            self.assertEqual(first.original_team_fhm_id, 5)
            self.assertEqual(first.round, 1)
            self.assertEqual(first.owner_team_fhm_id, 5)
            self.assertTrue(
                any(row.round == 11 and row.owner_team_fhm_id == 12 for row in added)
            )
            self.assertFalse(any(row.round == 10 for row in added))

    def test_describe_draft_pick_label(self) -> None:
        row = MagicMock(
            draft_year=2000,
            round=2,
            original_team_fhm_id=5,
            owner_team_fhm_id=12,
        )
        orig = MagicMock(abbreviation="STL")
        owner = MagicMock(abbreviation="BOS")
        label = describe_draft_pick_row(row, original_team=orig, owner_team=owner)
        self.assertIn("2000", label)
        self.assertIn("2", label)
        self.assertTrue("STL" in label or "BOS" in label)

    def test_drag_key_format(self) -> None:
        self.assertEqual(draft_pick_drag_key(42), "dpick:42")


if __name__ == "__main__":
    unittest.main()
