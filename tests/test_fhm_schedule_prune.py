"""Prune schedule games dropped from FHM exports between league years."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select

from app import create_app
from app.config import Config
from app.league_db import db
from app.models import Game, Season, Team
from scripts.import_pipeline.fhm_loader import import_games


class FhmSchedulePruneTests(unittest.TestCase):
    def test_import_games_prunes_rows_missing_from_current_schedules_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "test.db"
            raw = tmp_path / "raw"
            raw.mkdir()
            (raw / "schedules.csv").write_text(
                "GameId;LeagueId;Home;Away;Date;Played;Score_Home;Score_Away;Overtime;Shootout;Type\n"
                "9001;0;10;11;2000-10-01;0;;;0;0;Regular Season\n",
                encoding="utf-8",
            )

            class _TestConfig(Config):
                SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path.as_posix()}"
                TESTING = True

            app = create_app(_TestConfig)
            with app.app_context():
                try:
                    db.create_all()
                    season = Season(
                        fhm_season_id="fhm-league-0",
                        label="Test",
                        start_year=2000,
                        end_year=2001,
                        is_current=True,
                    )
                    home = Team(name="Home", abbreviation="HOM", slug="hom-t10", fhm_team_id=10)
                    away = Team(name="Away", abbreviation="AWY", slug="awy-t11", fhm_team_id=11)
                    stale = Game(
                        season_id=1,
                        home_team_id=1,
                        away_team_id=2,
                        fhm_game_id="8000",
                        fhm_league_id=0,
                        status="final",
                    )
                    db.session.add_all([season, home, away])
                    db.session.flush()
                    stale.season_id = season.id
                    stale.home_team_id = home.id
                    stale.away_team_id = away.id
                    db.session.add(stale)
                    db.session.commit()

                    teams_fhm = {10: home.id, 11: away.id}
                    with patch(
                        "scripts.import_pipeline.fhm_loader.commit_with_sqlite_retry",
                        side_effect=lambda session: session.commit(),
                    ):
                        import_games(raw, season, teams_fhm, league_filter=0)

                    total = db.session.scalar(select(func.count()).select_from(Game)) or 0
                    self.assertEqual(total, 1)
                    kept = db.session.scalar(select(Game).where(Game.fhm_game_id == "9001"))
                    self.assertIsNotNone(kept)
                    self.assertEqual(kept.status, "scheduled")
                finally:
                    db.session.remove()
                    db.drop_all()
                    for engine in db.engines.values():
                        engine.dispose()


if __name__ == "__main__":
    unittest.main()
