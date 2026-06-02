"""Player hover card boost badge coverage."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from flask import Flask


class PlayerHoverBoostBadgeTest(unittest.TestCase):
    def test_hover_payload_includes_gold_silver_boost_badge_fields(self) -> None:
        from app.routes import api

        player = MagicMock(
            id=42,
            full_name="Boosted Player",
            current_team_id=None,
            birth_date=None,
            fhm_player_id="42",
            position="C",
            boost_tier="gold",
            retired=False,
            overall_ability=15.0,
            overall_potential=16.0,
            shoots_catches="L",
            height_inches=72,
            weight_lbs=190,
            nationality="CAN",
        )
        session = MagicMock()
        session.get.return_value = player
        session.scalar.return_value = None
        app = Flask(__name__)
        app.config["LEAGUE_DISPLAY_NAME"] = "Test League"
        with app.app_context():
            with (
                patch.object(api, "db", MagicMock(session=session)),
                patch.object(api, "get_current_season", return_value=None),
                patch.object(api, "get_player_ratings_row", return_value=None),
                patch.object(api, "build_overall_cell_map_from_players", return_value={42: {"score": 82}}),
                patch.object(api, "_hover_recent_skater_seasons", return_value=[]),
                patch.object(api, "_latest_rs_season_stats_share", return_value=None),
                patch.object(api, "_contract_payload_for_share", return_value={"aav": None, "years_left": None}),
                patch.object(api, "position_ratings_display_list", return_value=[]),
                patch.object(api, "league_logo_url", return_value=""),
                patch.object(api, "_player_photo_url", return_value=""),
                patch.object(api, "url_for", side_effect=lambda endpoint, filename=None, **_kw: f"/static/{filename}" if endpoint == "static" else ""),
            ):
                out = api._build_player_hover_card_payload(42)

        self.assertEqual(out["boost_tier"], "gold")
        self.assertEqual(out["boost_badge_url"], "/static/img/boosts/gold-boost.png")
        self.assertEqual(out["boost_badge_label"], "Gold boost")

    def test_hover_frontend_renders_boost_badge(self) -> None:
        root = Path(__file__).resolve().parents[1]
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        self.assertIn("player-hover-card__boost-badge", js)
        self.assertIn("boost_badge_url", js)
        self.assertIn("player-hover-card--boosted", js)
        self.assertIn(".player-hover-card__boost-badge", css)


if __name__ == "__main__":
    unittest.main()
