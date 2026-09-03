"""Game preview payload for unplayed games."""
from __future__ import annotations

import unittest

from sqlalchemy import select

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Game


class GamePreviewPayloadTest(unittest.TestCase):
    def test_scheduled_cap_preview_api_returns_team_cards(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.app_context():
            game = db.session.scalars(
                select(Game).where(Game.status != "final").order_by(Game.id.desc()).limit(1)
            ).first()
            if game is None:
                self.skipTest("no scheduled Cap games in local DB")
            game_id = int(game.id)
        with app.test_client() as client:
            resp = client.get(f"/api/game/{game_id}/preview")
        self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True)[:500])
        payload = resp.get_json()
        self.assertIsNotNone(payload)
        self.assertIn("away", payload)
        self.assertIn("home", payload)
        self.assertIn("logo_url", payload["away"]["team"])
        self.assertIn("logo_url", payload["home"]["team"])


if __name__ == "__main__":
    unittest.main()
