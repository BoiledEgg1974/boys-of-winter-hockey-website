"""Player Gold/Silver/HoF markers persist in the site DB across league imports."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from app import create_app
from app.config import Config
from app.league_db import db
from app.models import Player
from app.services.player_boost_markers import (
    apply_site_markers_to_league_players,
    resolved_player_boost_tier,
    set_player_boost_tier,
)
from app.site_models import PlayerBoostMarker


class PlayerBoostMarkerPersistTests(unittest.TestCase):
    def test_site_marker_survives_cleared_league_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            league_db = root / "league.db"
            site_db = root / "site.db"

            class _TestConfig(Config):
                SQLALCHEMY_DATABASE_URI = f"sqlite:///{league_db.as_posix()}"
                SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{site_db.as_posix()}"
                TESTING = True
                LEAGUE_SLUG = "bowl-fantasy"
                SQLALCHEMY_BINDS = {}

            app = create_app(_TestConfig)
            with app.app_context():
                try:
                    db.create_all()
                    db.create_all(bind_key="site")
                    player = Player(
                        first_name="Mark",
                        last_name="Messier",
                        full_name="Mark Messier",
                        fhm_player_id="4101",
                        boost_tier="",
                    )
                    db.session.add(player)
                    db.session.commit()

                    set_player_boost_tier(player, "gold", user_id=7)
                    db.session.commit()

                    stored = db.session.scalars(
                        select(PlayerBoostMarker).where(
                            PlayerBoostMarker.league_slug == "bowl-fantasy",
                            PlayerBoostMarker.fhm_player_id == "4101",
                        )
                    ).first()
                    self.assertIsNotNone(stored)
                    assert stored is not None
                    self.assertEqual(stored.boost_tier, "gold")
                    self.assertEqual(stored.updated_by_user_id, 7)

                    player.boost_tier = ""
                    db.session.commit()
                    db.session.refresh(player)
                    self.assertEqual(player.boost_tier, "")
                    self.assertEqual(resolved_player_boost_tier(player), "gold")

                    restored = apply_site_markers_to_league_players(db.session)
                    db.session.commit()
                    db.session.refresh(player)
                    self.assertGreaterEqual(restored, 1)
                    self.assertEqual(player.boost_tier, "gold")

                    set_player_boost_tier(player, "", user_id=7)
                    db.session.commit()
                    player.boost_tier = "gold"
                    db.session.commit()
                    self.assertEqual(resolved_player_boost_tier(player), "")
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()

    def test_resolved_falls_back_to_player_column_without_site_row(self) -> None:
        player = Player(
            first_name="Wayne",
            last_name="Gretzky",
            full_name="Wayne Gretzky",
            fhm_player_id="99",
            boost_tier="silver",
        )
        self.assertEqual(resolved_player_boost_tier(player), "silver")


if __name__ == "__main__":
    unittest.main()
