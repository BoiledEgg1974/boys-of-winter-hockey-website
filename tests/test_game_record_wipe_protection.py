"""Boxscore wipe paths must promote game-record baselines before deleting rows.

Simulates a season-reset FHM import on an isolated DB: a record set in a game log
must survive both ``_clear_game_details`` (full boxscore wipe) and
``_delete_games_cascade`` (stale schedule prune) with no explicit sync by callers.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import func, select

from app import create_app
from app.config import Config
from app.league_db import db
from app.models import (
    Game,
    GameRecordBaseline,
    GameSkaterStat,
    Player,
    Season,
    Team,
)
from app.services.game_records import GameRecordMetric, resolve_game_record
from scripts.import_pipeline.fhm_loader import _clear_game_details, _delete_games_cascade


class GameRecordWipeProtectionTests(unittest.TestCase):
    def _seed_record_game(self) -> tuple[int, int]:
        """Create one final game with a 7-goal skater line; returns (game_id, player_id)."""
        season = Season(
            fhm_season_id="fhm-league-0",
            label="1999-00",
            start_year=1999,
            end_year=2000,
            is_current=True,
        )
        home = Team(name="Home", abbreviation="HOM", slug="hom-t10", fhm_team_id=10)
        away = Team(name="Away", abbreviation="AWY", slug="awy-t11", fhm_team_id=11)
        player = Player(
            first_name="Rocket",
            last_name="Record",
            full_name="Rocket Record",
            position="C",
        )
        db.session.add_all([season, home, away, player])
        db.session.flush()
        game = Game(
            season_id=season.id,
            home_team_id=home.id,
            away_team_id=away.id,
            fhm_game_id="7777",
            fhm_league_id=0,
            status="final",
            game_type="Regular Season",
            game_date=date(2000, 1, 15),
        )
        db.session.add(game)
        db.session.flush()
        line = GameSkaterStat(
            game_id=game.id,
            player_id=player.id,
            team_id=home.id,
            goals=7,
            assists=0,
        )
        db.session.add(line)
        db.session.commit()
        return int(game.id), int(player.id)

    def _run_in_fresh_app(self, body) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "test.db"

            class _TestConfig(Config):
                SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path.as_posix()}"
                TESTING = True

            app = create_app(_TestConfig)
            with app.app_context():
                try:
                    db.create_all()
                    body()
                finally:
                    db.session.remove()
                    db.drop_all()
                    for engine in db.engines.values():
                        engine.dispose()

    def test_clear_game_details_promotes_baselines_before_wipe(self) -> None:
        def body() -> None:
            _, player_id = self._seed_record_game()
            self.assertEqual(
                db.session.scalar(select(func.count()).select_from(GameRecordBaseline)) or 0, 0
            )

            _clear_game_details()

            self.assertEqual(
                db.session.scalar(select(func.count()).select_from(GameSkaterStat)) or 0, 0
            )
            metric = GameRecordMetric("goals", "Goals", "skater")
            holder = resolve_game_record(db.session, metric, "rs", "all")
            self.assertIsNotNone(holder)
            self.assertEqual(holder.value, 7.0)
            self.assertIsNotNone(holder.player)
            self.assertEqual(int(holder.player.id), player_id)
            self.assertEqual(holder.source, "baseline")

        self._run_in_fresh_app(body)

    def test_delete_games_cascade_promotes_baselines_before_prune(self) -> None:
        def body() -> None:
            game_id, player_id = self._seed_record_game()

            _delete_games_cascade([game_id])
            db.session.commit()

            self.assertIsNone(db.session.get(Game, game_id))
            metric = GameRecordMetric("goals", "Goals", "skater")
            holder = resolve_game_record(db.session, metric, "rs", "all")
            self.assertIsNotNone(holder)
            self.assertEqual(holder.value, 7.0)
            self.assertIsNotNone(holder.player)
            self.assertEqual(int(holder.player.id), player_id)
            # Pruned game's FK is nulled on the baseline, but the mark itself survives.
            row = db.session.scalar(
                select(GameRecordBaseline).where(
                    GameRecordBaseline.metric_key == "goals",
                    GameRecordBaseline.segment == "rs",
                    GameRecordBaseline.scope == "all",
                    GameRecordBaseline.player_kind == "skater",
                )
            )
            self.assertIsNotNone(row)
            self.assertIsNone(row.game_id)
            self.assertEqual(row.value, 7.0)

        self._run_in_fresh_app(body)


if __name__ == "__main__":
    unittest.main()
