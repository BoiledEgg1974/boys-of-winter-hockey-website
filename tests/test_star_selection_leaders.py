"""Star Selection Leaders homepage aggregation."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app
from app.config import Config
from app.league_db import db
from app.models import Game, GameSkaterStat, Player, Season, Team
from app.services.homepage_dashboard import build_star_selection_leaders
from flask import current_app


class StarSelectionLeadersTests(unittest.TestCase):
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

    def _seed_season_with_stars(self) -> int:
        season = Season(
            fhm_season_id="fhm-league-0",
            label="2024-25",
            start_year=2024,
            end_year=2025,
            is_current=True,
        )
        home = Team(name="Home", abbreviation="HOM", slug="hom-t10", fhm_team_id=10)
        away = Team(name="Away", abbreviation="AWY", slug="awy-t11", fhm_team_id=11)
        star_a = Player(
            first_name="Alice",
            last_name="Star",
            full_name="Alice Star",
            position="C",
            fhm_player_id="1001",
            current_team_id=None,
        )
        star_b = Player(
            first_name="Bob",
            last_name="Star",
            full_name="Bob Star",
            position="LW",
            fhm_player_id="1002",
            current_team_id=None,
        )
        db.session.add_all([season, home, away, star_a, star_b])
        db.session.flush()
        star_a.current_team_id = home.id
        star_b.current_team_id = away.id

        def _add_game(
            *,
            game_type: str,
            gdate: date,
            star1: int | None = None,
            star2: int | None = None,
            star3: int | None = None,
        ) -> Game:
            game = Game(
                season_id=season.id,
                home_team_id=home.id,
                away_team_id=away.id,
                fhm_game_id=f"g-{game_type}-{gdate.isoformat()}-{star1}-{star2}-{star3}",
                fhm_league_id=0,
                status="final",
                game_type=game_type,
                game_date=gdate,
                fhm_star1_player_id=star1,
                fhm_star2_player_id=star2,
                fhm_star3_player_id=star3,
            )
            db.session.add(game)
            db.session.flush()
            added_players: set[int] = set()
            if star1 == 1001 and star_a.id not in added_players:
                db.session.add(
                    GameSkaterStat(game_id=game.id, player_id=star_a.id, team_id=home.id, goals=1, assists=0)
                )
                added_players.add(star_a.id)
            if star2 == 1002 and star_b.id not in added_players:
                db.session.add(
                    GameSkaterStat(game_id=game.id, player_id=star_b.id, team_id=away.id, goals=0, assists=1)
                )
                added_players.add(star_b.id)
            if star3 == 1001 and star_a.id not in added_players:
                db.session.add(
                    GameSkaterStat(game_id=game.id, player_id=star_a.id, team_id=home.id, goals=0, assists=1)
                )
                added_players.add(star_a.id)
            return game

        # Regular season: Alice 1st + 3rd, Bob 2nd
        _add_game(
            game_type="Regular Season",
            gdate=date(2024, 10, 10),
            star1=1001,
            star2=1002,
            star3=1001,
        )
        # Second RS game: Alice 1st again
        _add_game(
            game_type="Regular Season",
            gdate=date(2024, 10, 15),
            star1=1001,
            star2=None,
            star3=None,
        )
        # Pre-season and playoffs should be ignored
        _add_game(
            game_type="Pre-Season",
            gdate=date(2024, 9, 20),
            star1=1002,
            star2=1002,
            star3=1002,
        )
        _add_game(
            game_type="Playoffs",
            gdate=date(2025, 4, 10),
            star1=1002,
            star2=1002,
            star3=1002,
        )
        # Unresolved FHM ID should be skipped
        _add_game(
            game_type="Regular Season",
            gdate=date(2024, 10, 20),
            star1=9999,
            star2=None,
            star3=None,
        )
        db.session.commit()
        return int(season.id)

    def test_empty_season_returns_empty_list(self) -> None:
        def body() -> None:
            season = Season(
                fhm_season_id="fhm-empty",
                label="Empty",
                start_year=2024,
                end_year=2025,
                is_current=True,
            )
            db.session.add(season)
            db.session.commit()
            rows = build_star_selection_leaders(db.session, season.id)
            self.assertEqual(rows, [])

        self._run_in_fresh_app(body)

    def test_regular_season_scoring_and_top_limit(self) -> None:
        def body() -> None:
            season_id = self._seed_season_with_stars()
            with current_app.test_request_context():
                rows = build_star_selection_leaders(db.session, season_id, limit=10)
            self.assertEqual(len(rows), 2)
            alice = rows[0]
            bob = rows[1]
            self.assertEqual(alice["player"], "Alice Star")
            self.assertEqual(alice["star1"], 2)
            self.assertEqual(alice["star2"], 0)
            self.assertEqual(alice["star3"], 1)
            self.assertEqual(alice["points"], 11)  # 2*5 + 1*1
            self.assertEqual(alice["team"], "HOM")
            self.assertEqual(bob["player"], "Bob Star")
            self.assertEqual(bob["star1"], 0)
            self.assertEqual(bob["star2"], 1)
            self.assertEqual(bob["star3"], 0)
            self.assertEqual(bob["points"], 3)
            self.assertEqual(bob["team"], "AWY")

        self._run_in_fresh_app(body)

    def test_top_ten_cap(self) -> None:
        def body() -> None:
            season = Season(
                fhm_season_id="fhm-cap",
                label="Cap",
                start_year=2024,
                end_year=2025,
                is_current=True,
            )
            home = Team(name="Home", abbreviation="HOM", slug="hom-cap", fhm_team_id=10)
            away = Team(name="Away", abbreviation="AWY", slug="awy-cap", fhm_team_id=11)
            db.session.add_all([season, home, away])
            db.session.flush()
            players: list[Player] = []
            for i in range(12):
                pl = Player(
                    first_name=f"P{i}",
                    last_name="Test",
                    full_name=f"P{i} Test",
                    position="C",
                    fhm_player_id=str(2000 + i),
                    current_team_id=home.id,
                )
                players.append(pl)
            db.session.add_all(players)
            db.session.flush()
            for i, pl in enumerate(players):
                game = Game(
                    season_id=season.id,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    fhm_game_id=f"cap-{i}",
                    fhm_league_id=0,
                    status="final",
                    game_type="Regular Season",
                    game_date=date(2024, 11, 1 + i),
                    fhm_star1_player_id=int(pl.fhm_player_id),
                )
                db.session.add(game)
                db.session.flush()
                db.session.add(
                    GameSkaterStat(game_id=game.id, player_id=pl.id, team_id=home.id, goals=1, assists=0)
                )
            db.session.commit()
            with current_app.test_request_context():
                rows = build_star_selection_leaders(db.session, season.id, limit=10)
            self.assertEqual(len(rows), 10)

        self._run_in_fresh_app(body)

    def test_homepage_summary_includes_key(self) -> None:
        from app.config import make_league_config

        app = create_app(make_league_config("bowl-historical"))
        with app.test_client() as client:
            data = client.get("/api/homepage/summary?segment=rs").get_json()
        self.assertIn("star_selection_leaders", data)
        self.assertIsInstance(data["star_selection_leaders"], list)


if __name__ == "__main__":
    unittest.main()
