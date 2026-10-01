"""Relegation AP catalog legacy duplicate retirement."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.ap_service import (
    _normalize_catalog_title,
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


if __name__ == "__main__":
    unittest.main()
