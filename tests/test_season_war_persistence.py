"""Permanent player_season_war register: upsert, finalize, no downgrade."""
from __future__ import annotations

import unittest
from datetime import datetime

from sqlalchemy import select

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Player, PlayerSeasonWar
from app.services.season_war import _should_replace, _upsert_row


class SeasonWarReplaceRulesTests(unittest.TestCase):
    def test_full_formula_replaces_legacy(self) -> None:
        self.assertTrue(_should_replace(None, formula_version="legacy", is_finalized=True))
        existing = PlayerSeasonWar(
            player_id=1,
            league_slug="bowl-cap",
            season_year=2010,
            stat_segment="rs",
            is_goalie=False,
            war_pct=40,
            formula_version="legacy",
            gp=50,
            is_finalized=True,
            metrics_json="{}",
            updated_at=datetime.utcnow(),
        )
        self.assertTrue(
            _should_replace(existing, formula_version="full", is_finalized=True)
        )
        full_existing = PlayerSeasonWar(
            player_id=1,
            league_slug="bowl-cap",
            season_year=2011,
            stat_segment="rs",
            is_goalie=False,
            war_pct=80,
            formula_version="full",
            gp=50,
            is_finalized=True,
            metrics_json="{}",
            updated_at=datetime.utcnow(),
        )
        self.assertFalse(
            _should_replace(full_existing, formula_version="legacy", is_finalized=True)
        )


class SeasonWarPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(make_league_config("bowl-cap"))
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()
        player = db.session.scalar(select(Player).order_by(Player.id.desc()).limit(1))
        if player is None:
            self.skipTest("No players in league DB")
        self.player_id = int(player.id)

    def tearDown(self) -> None:
        db.session.remove()
        self.ctx.pop()

    def test_finalize_blocks_live_overwrite(self) -> None:
        now = datetime.utcnow()
        pid = int(self.player_id)
        _upsert_row(
            db.session,
            league_slug="bowl-cap",
            player_id=pid,
            season_year=2020,
            segment="rs",
            is_goalie=False,
            war_pct=70,
            formula_version="full",
            gp=80,
            metrics={"game_rating": 65.0},
            is_finalized=True,
            now=now,
        )
        db.session.commit()
        changed = _upsert_row(
            db.session,
            league_slug="bowl-cap",
            player_id=pid,
            season_year=2020,
            segment="rs",
            is_goalie=False,
            war_pct=55,
            formula_version="full",
            gp=80,
            metrics={"game_rating": 50.0},
            is_finalized=False,
            now=now,
        )
        db.session.commit()
        row = db.session.scalars(select(PlayerSeasonWar).limit(1)).first()
        self.assertFalse(changed)
        self.assertEqual(int(row.war_pct or 0), 70)
        self.assertTrue(row.is_finalized)


if __name__ == "__main__":
    unittest.main()
