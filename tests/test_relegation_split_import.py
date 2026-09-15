"""Multi-league FHM import, injuries, and movement watch for BOWL-Relegation."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.models import InjuryType, LeagueMeta, Player, PlayerInjury, Team, db
from app.services.relegation import (
    MOVEMENT_TEAMS,
    build_movement_watch,
    get_tier_config,
    relegation_features_enabled,
)
from scripts.import_pipeline.fhm_loader import import_injuries, relegation_tier_league_ids


class RelegationTierDetectTests(unittest.TestCase):
    def test_relegation_tier_league_ids_from_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            (raw / "league_data.csv").write_text(
                "LeagueId;Name;Abbr\n0;BOWL-Upper;BLUP\n1;BOWL-Lower;BLOW\n2;AHL;AHL\n",
                encoding="utf-8",
            )
            self.assertEqual(relegation_tier_league_ids(raw), (0, 1))

    def test_relegation_tier_league_ids_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(relegation_tier_league_ids(Path(tmp)))


class RelegationMovementWatchTests(unittest.TestCase):
    def test_build_movement_watch_returns_two_teams_each_side(self) -> None:
        session = MagicMock()
        from app.services.relegation import RelegationTierConfig

        config = RelegationTierConfig(
            mode="league_id",
            upper_league_ids=frozenset({0}),
            lower_league_ids=frozenset({1}),
            upper_conference_ids=frozenset(),
            lower_conference_ids=frozenset(),
            upper_label="BOWL-Upper",
            lower_label="BOWL-Lower",
            combined_league_ids=(0, 1),
        )

        def _team(name: str, lid: int, tid: int) -> SimpleNamespace:
            return SimpleNamespace(
                id=tid,
                fhm_league_id=lid,
                fhm_conference_id=None,
                full_display_name=lambda: name,
            )

        def _st(team: SimpleNamespace, tid: int, pts: int) -> SimpleNamespace:
            return SimpleNamespace(
                team=team,
                team_id=tid,
                pts=pts,
                w=pts // 2,
                gf=pts + 5,
                ga=pts - 5,
                shootout_wins=0,
                standing_gp_display=lambda: 20,
            )

        upper_rows = [
            _st(_team("U1", 0, 1), 1, 30),
            _st(_team("U2", 0, 2), 2, 28),
            _st(_team("U3", 0, 3), 3, 20),
            _st(_team("U4", 0, 4), 4, 18),
        ]
        lower_rows = [
            _st(_team("L1", 1, 5), 5, 40),
            _st(_team("L2", 1, 6), 6, 38),
            _st(_team("L3", 1, 7), 7, 30),
        ]
        session.scalars.return_value.all.return_value = upper_rows + lower_rows
        with patch("app.services.relegation._count_remaining_rs_games", return_value=0):
            with patch("app.services.relegation._lower_playoff_leader", return_value=lower_rows[0]):
                movement = build_movement_watch(session, 1, config)
        self.assertEqual(len(movement["relegation_danger"]), MOVEMENT_TEAMS)
        self.assertEqual(len(movement["promotion_watch"]), MOVEMENT_TEAMS)
        self.assertEqual(movement["relegation_danger"][0]["rank"], 3)
        self.assertEqual(movement["promotion_watch"][0]["rank"], 1)


class InjuryImportTests(unittest.TestCase):
    def test_import_injuries_snapshot(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            (raw / "injuries_data.csv").write_text(
                "Injury Id;Name;Min Days;Max Days\n5;Sprained Knee;7;14\n",
                encoding="utf-8",
            )
            (raw / "player_injuries.csv").write_text(
                "PlayerId;Team Id;Franchise Id;Injury Id;Recovery Time\n101;99;99;5;10\n",
                encoding="utf-8",
            )
            with app.app_context():
                db.create_all()
                suffix = "x9z8"
                team = Team(
                    fhm_team_id=f"inj-test-{suffix}",
                    slug=f"inj-test-t{suffix}",
                    abbreviation="INJ",
                    name="Inj Test",
                )
                player = Player(
                    fhm_player_id=f"inj-test-p{suffix}",
                    first_name="Pat",
                    last_name="Test",
                    full_name="Pat Test",
                )
                db.session.add(team)
                db.session.add(player)
                db.session.commit()
                players_fhm = {101: player.id}
                teams_fhm = {99: team.id}
                n = import_injuries(raw, players_fhm, teams_fhm, league_filter=(0, 1))
                self.assertEqual(n, 1)
                row = db.session.scalars(db.select(PlayerInjury)).first()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.recovery_days, 10)
                it = db.session.get(InjuryType, row.injury_type_id)
                self.assertIsNotNone(it)
                assert it is not None
                self.assertEqual(it.name, "Sprained Knee")


class RelegationAutoEnableTests(unittest.TestCase):
    def test_features_enabled_when_tier_meta_present(self) -> None:
        from app.services.relegation import RelegationTierConfig

        with patch(
            "app.services.relegation.get_tier_config",
            return_value=RelegationTierConfig(
                mode="league_id",
                upper_league_ids=frozenset({0}),
                lower_league_ids=frozenset({1}),
                upper_conference_ids=frozenset(),
                lower_conference_ids=frozenset(),
                upper_label="BOWL-Upper",
                lower_label="BOWL-Lower",
                combined_league_ids=(0, 1),
            ),
        ):
            self.assertTrue(relegation_features_enabled("bowl-fantasy"))


if __name__ == "__main__":
    unittest.main()
