"""Public list pages ignore ?expanded=1 unless the visitor is signed in."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app import create_app
from app.config import make_league_config
from app.routes.main import _honor_expanded_query


class PublicListExpandTest(unittest.TestCase):
    def test_anonymous_request_does_not_honor_expanded(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.test_request_context("/records?expanded=1"):
            self.assertFalse(_honor_expanded_query())

    def test_routes_gate_expanded_on_login(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "app" / "routes" / "main.py").read_text(
            encoding="utf-8"
        )
        self.assertGreaterEqual(text.count("and _honor_expanded_query()"), 3)

    def test_nginx_blocks_scraper_user_agents(self) -> None:
        root = Path(__file__).resolve().parents[1]
        limits = (root / "deploy" / "vps" / "nginx-bowl-limits.conf").read_text(encoding="utf-8")
        site = (root / "deploy" / "vps" / "nginx-bowl-live-443.conf").read_text(encoding="utf-8")
        self.assertIn("python-httpx", limits)
        self.assertIn("Amzn-SearchBot", limits)
        self.assertIn("bowl_scraper_ua", limits)
        self.assertIn('~*Macintosh; Intel Mac OS X', limits)
        self.assertIn("bowl_deny_bot", limits)
        self.assertIn("bowl_heavy_conn", limits)
        self.assertIn("bowl-cloudflare.conf", site)
        self.assertIn("bowl_fa_global", site)
        self.assertIn("limit_conn bowl_heavy_conn", site)


class RecordsFetchLimitTest(unittest.TestCase):
    def test_skater_fetch_applies_sql_limit(self) -> None:
        from app.services.all_time_records import fetch_skater_all_time

        session = MagicMock()
        result = MagicMock()
        result.unique.return_value = []
        session.execute.return_value = result
        session.scalars.return_value.all.return_value = []

        fetch_skater_all_time(session, "rs", "points", "desc", limit=100)
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("LIMIT 100", compiled.upper().replace("\n", " "))


if __name__ == "__main__":
    unittest.main()
