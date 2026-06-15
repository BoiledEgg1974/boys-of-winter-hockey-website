"""PythonAnywhere MySQL URL normalization."""
from __future__ import annotations

import unittest

from app.config import normalize_site_database_url


class SiteMysqlUrlTests(unittest.TestCase):
    def test_decodes_percent24_to_dollar_in_database_name(self) -> None:
        raw = "mysql+pymysql://user:pass@host/BoiledEgg1974%24bowlsite"
        fixed = normalize_site_database_url(raw)
        self.assertIn("BoiledEgg1974$bowlsite", fixed)
        self.assertNotIn("%24", fixed)

    def test_leaves_sqlite_urls_unchanged(self) -> None:
        raw = "sqlite:///instance/site_membership.db"
        self.assertEqual(normalize_site_database_url(raw), raw)


if __name__ == "__main__":
    unittest.main()
