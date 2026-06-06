"""Trade Market / Trade Tool are mounted on every league site."""
from __future__ import annotations

import unittest
from pathlib import Path

from app import create_app
from app.config import LEAGUES, make_league_config
from app.services.discord_events import DEFAULT_EVENT_CHANNEL_KEY, DEFAULT_EVENT_KEYS
from scripts.league_discord_bot.formatters import format_discord_message


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
            "site_gm.trade_market_chat_start",
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

    def test_discord_trade_market_events_registered_for_all_leagues(self) -> None:
        self.assertIn("trade_market_selling_posted", DEFAULT_EVENT_KEYS)
        self.assertIn("trade_market_buying_posted", DEFAULT_EVENT_KEYS)

    def test_confirmed_trade_discord_event_uses_confirm_trade_channel(self) -> None:
        self.assertIn("confirmed_trade", DEFAULT_EVENT_KEYS)
        self.assertEqual(DEFAULT_EVENT_CHANNEL_KEY.get("confirmed_trade"), "confirm-trade")

    def test_confirmed_trade_discord_formatter_is_text_post(self) -> None:
        msg = format_discord_message(
            {
                "league_slug": "bowl-historical",
                "event_key": "confirmed_trade",
                "payload": {
                    "title": "Trade: Oakland Seals ↔ Boston Bruins",
                    "body": "Oakland sends a pick.\n\nBoston sends a player.",
                    "team_abbrev": "OAK",
                    "team_name": "Oakland Seals",
                    "fhm_team_id": 120,
                    "team_url": "https://www.bowlhockey.com/bowl-historical/team/oakland-seals",
                },
            }
        )
        content = msg.get("content", "")
        self.assertIn("Trade: Oakland Seals", content)
        self.assertIn("[OAK](https://www.bowlhockey.com/bowl-historical/team/oakland-seals)", content)
        self.assertIn("Oakland sends a pick", content)
        self.assertNotIn("embeds", msg)

    def test_trade_market_json_posts_include_csrf_header(self) -> None:
        """Flask-WTF CSRFProtect requires X-CSRFToken on JSON POST (body alone is not enough)."""
        path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "trade_market.html"
        )
        text = path.read_text(encoding="utf-8")
        self.assertGreaterEqual(
            text.count('"X-CSRFToken": csrf'),
            2,
            "selling and buying save fetch calls must send X-CSRFToken",
        )

    def test_trade_market_template_has_owner_edit_delete_controls(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "trade_market.html"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn('data-active-team-id="{{ active_team_id or \'\' }}"', text)
        self.assertIn("is_site_admin or owns_market_row", text)
        self.assertIn("owns_market_row", text)
        self.assertIn("(row.user_id|int) == (current_user.id|int)", text)
        self.assertIn('data-team-id="{{ row.team_id }}"', text)
        self.assertIn("trade-market-all-listings", text)
        self.assertIn("trade-market-all-buying", text)
        for selector in (
            "trade-market-edit-selling",
            "trade-market-delete-selling",
            "trade-market-edit-buying",
            "trade-market-delete-buying",
        ):
            self.assertIn(selector, text)

    def test_trade_market_template_has_chat_controls(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "trade_market.html"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn('data-chat-url="{{ url_for(\'site_gm.trade_market_chat_start\') }}"', text)
        self.assertIn("trade-market-chat", text)
        self.assertIn("dialog-chat", text)
        self.assertIn("peer_user_id: chatState.peerUserId", text)

    def test_trade_market_template_uses_card_layout_and_shared_rating_classes(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "templates"
            / "trade_market.html"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("trade-market-team-list", text)
        self.assertIn("trade-market-team-block__grid", text)
        self.assertIn("trade-market-team-block__col-title--buying", text)
        self.assertIn("trade-market-team-block__col-title--selling", text)
        self.assertIn("stats-player-cell__name trade-market-asset__name", text)
        self.assertIn("stats-badge stats-badge--rating stats-badge--overall", text)
        self.assertIn("stats-ova__score trade-market-rating__value", text)
        self.assertIn("trade-market-sortbar", text)
        self.assertIn("class=\"wants-in\"", text)
        self.assertIn("top-four RD", text)
        self.assertNotIn("class=\"want-cb\"", text)

    def test_trade_market_public_route_hides_guest_gm_details(self) -> None:
        route_path = Path(__file__).resolve().parents[1] / "app" / "routes" / "site_portal.py"
        route_text = route_path.read_text(encoding="utf-8")
        self.assertIn('if not current_user.is_authenticated:', route_text)
        self.assertIn('row["gm_name"] = ""', route_text)
        self.assertIn("can_show_gm_names=current_user.is_authenticated", route_text)
        self.assertIn("cleanup_stale_selling_listings", route_text)

        template_path = Path(__file__).resolve().parents[1] / "app" / "templates" / "trade_market.html"
        template_text = template_path.read_text(encoding="utf-8")
        self.assertIn("can_show_gm_names and block.gm_name", template_text)
        self.assertIn("can_message_gms and block.user_id", template_text)

    def test_trade_market_listing_has_posted_game_date_column(self) -> None:
        model_path = Path(__file__).resolve().parents[1] / "app" / "site_models.py"
        db_utils_path = Path(__file__).resolve().parents[1] / "app" / "db_utils.py"
        self.assertIn("posted_game_date", model_path.read_text(encoding="utf-8"))
        self.assertIn("posted_game_date DATE", db_utils_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
