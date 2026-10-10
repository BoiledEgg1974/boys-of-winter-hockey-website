"""Relegation AP catalog legacy duplicate retirement."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.ap_service import (
    _catalog_match_key,
    _normalize_catalog_title,
    _pick_fantasy_catalog_duplicate_keeper,
    _retire_legacy_fantasy_catalog_entries,
)


class ApCatalogDedupeTest(unittest.TestCase):
    def test_retire_legacy_fantasy_catalog_entries(self) -> None:
        rows = {
            _normalize_catalog_title(t): SimpleNamespace(title=t, is_active=True)
            for t in (
                "Supplemental Staff",
                "Supplemental Staff Hiring",
                "Development / Market Package",
                "Market / Fan / Media +1",
                "Major Customization",
            )
        }
        changed = _retire_legacy_fantasy_catalog_entries(rows)
        self.assertTrue(changed)
        self.assertFalse(rows[_normalize_catalog_title("Supplemental Staff")].is_active)
        self.assertFalse(rows[_normalize_catalog_title("Development / Market Package")].is_active)
        self.assertFalse(rows[_normalize_catalog_title("Major Customization")].is_active)
        self.assertTrue(rows[_normalize_catalog_title("Supplemental Staff Hiring")].is_active)
        self.assertTrue(rows[_normalize_catalog_title("Market / Fan / Media +1")].is_active)

    def test_catalog_match_key_ignores_trailing_period(self) -> None:
        a = "Create a 4-Star Potential Player"
        b = "Create a 4-Star Potential Player."
        self.assertEqual(_catalog_match_key(a), _catalog_match_key(b))

    def test_pick_fantasy_duplicate_keeper_prefers_canonical_title(self) -> None:
        rows = [
            SimpleNamespace(
                id=2,
                title="Create a 4-Star Potential Player.",
                cost_ap=6000,
                is_active=True,
            ),
            SimpleNamespace(
                id=1,
                title="Create a 4-Star Potential Player",
                cost_ap=4000,
                is_active=True,
            ),
        ]
        keeper = _pick_fantasy_catalog_duplicate_keeper(rows)  # type: ignore[arg-type]
        self.assertEqual(keeper.id, 1)
        self.assertEqual(keeper.title, "Create a 4-Star Potential Player")


if __name__ == "__main__":
    unittest.main()
