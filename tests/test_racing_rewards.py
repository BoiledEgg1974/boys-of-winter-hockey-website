"""Formula AP reward schedules match the game: race 10→1, circuit 1000→10."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.config import Config
from app.league_db import db
from app.racing_models import RacingRewardTier
from app.services.racing_csv import formula_circuit_ap_for_rank, formula_circuit_channel_points_for_rank
from app.services.racing_rewards import (
    SCHEDULE_CIRCUIT_AP,
    SCHEDULE_CIRCUIT_CP,
    SCHEDULE_RACE_AP,
    default_tiers_for_league,
    ensure_default_reward_tiers,
    get_schedule_table,
)


class FormulaRewardDefaultTests(unittest.TestCase):
    def test_race_ap_is_10_down_to_1(self) -> None:
        race = dict(default_tiers_for_league("bowl-formula")[SCHEDULE_RACE_AP])
        self.assertEqual([race[p] for p in range(1, 11)], [10, 9, 8, 7, 6, 5, 4, 3, 2, 1])

    def test_circuit_ap_scales_1000_to_10_over_31(self) -> None:
        circuit = dict(default_tiers_for_league("bowl-formula")[SCHEDULE_CIRCUIT_AP])
        self.assertEqual(len(circuit), 31)
        self.assertEqual(circuit[1], 1000)
        self.assertEqual(circuit[31], 10)
        self.assertEqual(circuit[16], formula_circuit_ap_for_rank(16))
        self.assertEqual(circuit[16], 505)

    def test_circuit_channel_points_scale_p11_3000_to_p31_300(self) -> None:
        circuit_cp = dict(default_tiers_for_league("bowl-formula")[SCHEDULE_CIRCUIT_CP])
        self.assertEqual(circuit_cp[11], 3000)
        self.assertEqual(circuit_cp[31], 300)
        self.assertEqual(circuit_cp[12], 2865)
        self.assertNotIn(1, circuit_cp)
        self.assertEqual(formula_circuit_channel_points_for_rank(10), 0)
        self.assertEqual(formula_circuit_channel_points_for_rank(32), 0)

    def test_derby_circuit_ap_stays_six_place(self) -> None:
        circuit = dict(default_tiers_for_league("bowl-demolition")[SCHEDULE_CIRCUIT_AP])
        self.assertEqual(circuit, {1: 30, 2: 25, 3: 20, 4: 15, 5: 10, 6: 5})


class FormulaRewardUpgradeTests(unittest.TestCase):
    def test_stale_six_place_circuit_ap_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            class _TestConfig(Config):
                SQLALCHEMY_DATABASE_URI = f"sqlite:///{(root / 'league.db').as_posix()}"
                SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{(root / 'site.db').as_posix()}"
                TESTING = True
                LEAGUE_SLUG = "bowl-formula"
                RAW_IMPORT_DIR = root
                SQLALCHEMY_BINDS = {}

            app = create_app(_TestConfig)
            with app.app_context():
                try:
                    db.session.query(RacingRewardTier).filter(
                        RacingRewardTier.schedule_key == SCHEDULE_CIRCUIT_AP
                    ).delete()
                    db.session.query(RacingRewardTier).filter(
                        RacingRewardTier.schedule_key == SCHEDULE_RACE_AP
                    ).delete()
                    for place, amount in ((1, 30), (2, 25), (3, 20), (4, 15), (5, 10), (6, 5)):
                        db.session.add(
                            RacingRewardTier(
                                schedule_key=SCHEDULE_CIRCUIT_AP, place=place, amount=amount
                            )
                        )
                    for place, amount in ((1, 10), (2, 8), (3, 6), (4, 5), (5, 4), (6, 3), (7, 2), (8, 1)):
                        db.session.add(
                            RacingRewardTier(
                                schedule_key=SCHEDULE_RACE_AP, place=place, amount=amount
                            )
                        )
                    db.session.commit()
                    ensure_default_reward_tiers(db.session, league_slug="bowl-formula")
                    db.session.commit()
                    circuit = get_schedule_table(db.session, SCHEDULE_CIRCUIT_AP)
                    race = get_schedule_table(db.session, SCHEDULE_RACE_AP)
                    self.assertEqual(circuit[1], 1000)
                    self.assertEqual(circuit[31], 10)
                    self.assertEqual(len(circuit), 31)
                    self.assertEqual([race[p] for p in range(1, 11)], [10, 9, 8, 7, 6, 5, 4, 3, 2, 1])
                    db.session.query(RacingRewardTier).filter(
                        RacingRewardTier.schedule_key == SCHEDULE_CIRCUIT_CP
                    ).delete()
                    for place, amount in ((1, 1000), (2, 800), (3, 600), (4, 400), (5, 200)):
                        db.session.add(
                            RacingRewardTier(
                                schedule_key=SCHEDULE_CIRCUIT_CP, place=place, amount=amount
                            )
                        )
                    db.session.commit()
                    ensure_default_reward_tiers(db.session, league_slug="bowl-formula")
                    db.session.commit()
                    cp = get_schedule_table(db.session, SCHEDULE_CIRCUIT_CP)
                    self.assertEqual(cp[11], 3000)
                    self.assertEqual(cp[31], 300)
                    self.assertNotIn(1, cp)
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


if __name__ == "__main__":
    unittest.main()
