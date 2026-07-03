"""Staff ratings CSV parsing (duplicate StaffId rows from FHM exports)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.team_staff_csv import _read_staff_ratings_by_id


class StaffRatingsCsvTest(unittest.TestCase):
    def test_first_row_wins_when_staffid_duplicated(self) -> None:
        """FHM appends a second ratings block; the first row matches in-game values."""
        csv_text = (
            "StaffId;Coach;Coaching G;Coaching Defense;Coaching Forwards;"
            "Coaching Prospects;Def Skills;Off Skills;Phy Training;"
            "Player Management;Motivation;Trainer Skill;Evaluate Abilities;Evaluate Potential\n"
            "150;20;9;11;6;7;15;5;6;4;7;0;;13;12\n"
            "150;20;6;13;13;20;15;15;13;7;17;1;;13;12\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "staff_ratings.csv"
            path.write_text(csv_text, encoding="utf-8")
            by_id = _read_staff_ratings_by_id(path)

        row = by_id["150"]
        self.assertEqual(row["coaching_g"], "9")
        self.assertEqual(row["coaching_defense"], "11")
        self.assertEqual(row["coaching_forwards"], "6")
        self.assertEqual(row["coaching_prospects"], "7")
        self.assertEqual(row["off_skills"], "5")
        self.assertEqual(row["motivation"], "7")

    def test_fix_evaluate_columns_still_applies(self) -> None:
        csv_text = (
            "StaffId;Coach;Trainer Skill;Evaluate Abilities;Evaluate Potential\n"
            "1;18;0;;14;15\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "staff_ratings.csv"
            path.write_text(csv_text, encoding="utf-8")
            by_id = _read_staff_ratings_by_id(path)

        row = by_id["1"]
        self.assertEqual(row["evaluate_abilities"], "14")
        self.assertEqual(row["evaluate_potential"], "15")


if __name__ == "__main__":
    unittest.main()
