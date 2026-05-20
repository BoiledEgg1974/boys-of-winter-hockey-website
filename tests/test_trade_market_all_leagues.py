"""Trade Market / Trade Tool are mounted on every league site."""
from __future__ import annotations

import unittest
from pathlib import Path

from app import create_app
from app.config import LEAGUES, make_league_config
from app.services.draft_pick_ownership import DRAFT_PICK_CSV_NAME
from app.services.discord_events import DEFAULT_EVENT_KEYS


class TradeMarketAllLeaguesTest(unittest.TestCase):
    def test_each_league_mount_exposes_trade_routes(self) -> None:
        expected = {
            "site_gm.trade_tool",
            "site_gm.trade_market_page",
            "site_gm.ai_trade_tool",
            "site_gm.trade_tool_assets",
            "site_gm.trade_market_assets",
            "site_gm.trade_market_selling_save",
            "site_gm.trade_market_buying_save",
        }
        for entry in LEAGUES:
            app = create_app(make_league_config(entry.slug))
            with app.app_context():
                self.assertEqual(app.config["LEAGUE_SLUG"], entry.slug)
                names = {rule.endpoint for rule in app.url_map.iter_rules()}
                missing = expected - names
                self.assertFalse(
                    missing,
                    f"{entry.slug} missing routes: {sorted(missing)}",
                )

    def test_each_league_has_draft_pick_ownership_csv_template(self) -> None:
        for entry in LEAGUES:
            path = (
                Path(__file__).resolve().parents[1]
                / "data"
                / "imports"
                / "raw"
                / entry.raw_import_dir
                / DRAFT_PICK_CSV_NAME
            )
            self.assertTrue(
                path.is_file(),
                f"Expected {path} for {entry.slug}",
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("10th Round", text)
            self.assertIn("Year", text)

    def test_discord_trade_market_events_registered_for_all_leagues(self) -> None:
        self.assertIn("trade_market_selling_posted", DEFAULT_EVENT_KEYS)
        self.assertIn("trade_market_buying_posted", DEFAULT_EVENT_KEYS)


if __name__ == "__main__":
    unittest.main()
