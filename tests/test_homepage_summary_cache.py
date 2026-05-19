"""Homepage summary cache helpers."""
from __future__ import annotations

import unittest

from app.services.homepage_summary_cache import _strip_volatile_fields


class HomepageSummaryCacheTests(unittest.TestCase):
    def test_strip_volatile_fields(self) -> None:
        body = {
            "leaders": {"goals": []},
            "around_the_league": {"articles": []},
            "module_settings": {"visibility": {}},
            "ticker_items": [{"text": "x"}],
        }
        core = _strip_volatile_fields(body)
        self.assertIn("leaders", core)
        self.assertNotIn("around_the_league", core)
        self.assertNotIn("module_settings", core)
        self.assertNotIn("ticker_items", core)


if __name__ == "__main__":
    unittest.main()
