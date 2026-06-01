"""Draft-pick ownership helpers (Trade Market / Trade Tool)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.draft_pick_ownership import (
    describe_draft_pick_row,
    draft_pick_drag_key,
)


class DraftPickOwnershipHelperTest(unittest.TestCase):
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
