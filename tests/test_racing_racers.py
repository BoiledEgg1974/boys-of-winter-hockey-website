"""Roster linking must drop leftover stubs such as a renamed driver."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from app import create_app
from app.config import Config
from app.league_db import db
from app.racing_models import RacingNameAlias, RacingRacer
from app.services.racing_racers import (
    assign_racers_to_gms,
    delete_racer,
    ensure_alias,
    identity_match_score,
    link_roster_txt,
)


class RacingRosterPruneTests(unittest.TestCase):
    def test_link_roster_removes_stub_not_on_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            roster = root / "roster.txt"
            roster.write_text("4|GreedyFish\n33|Joey\n", encoding="utf-8")

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
                    db.create_all()
                    db.create_all(bind_key="site")
                    kings = RacingRacer(display_name="Kings", user_id=None, is_active=True)
                    greedy = RacingRacer(display_name="GreedyFish", user_id=None, is_active=True)
                    cap_gm = RacingRacer(
                        display_name="CapOnlyGM",
                        user_id=99,
                        ap_league_slug="bowl-cap",
                        ap_team_id=1,
                        is_active=True,
                    )
                    db.session.add_all([kings, greedy, cap_gm])
                    db.session.flush()
                    ensure_alias(db.session, kings, "Kings")
                    ensure_alias(db.session, greedy, "GreedyFish")
                    db.session.commit()

                    stats = link_roster_txt(db.session, roster, prune_missing=True)
                    db.session.commit()
                    self.assertIn("Kings", stats["pruned"])

                    names = {
                        r.display_name
                        for r in db.session.scalars(select(RacingRacer)).all()
                    }
                    self.assertNotIn("Kings", names)
                    self.assertIn("GreedyFish", names)
                    self.assertIn("Joey", names)
                    self.assertIn("CapOnlyGM", names)
                    self.assertIsNone(
                        db.session.scalar(
                            select(RacingNameAlias).where(RacingNameAlias.alias_key == "kings")
                        )
                    )
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()

    def test_delete_racer_drops_row(self) -> None:
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
                    db.create_all()
                    db.create_all(bind_key="site")
                    kings = RacingRacer(display_name="Kings", user_id=None, is_active=True)
                    db.session.add(kings)
                    db.session.flush()
                    ensure_alias(db.session, kings, "Kings")
                    db.session.commit()
                    rid = int(kings.id)
                    self.assertEqual(delete_racer(db.session, rid), "Kings")
                    db.session.commit()
                    self.assertIsNone(db.session.get(RacingRacer, rid))
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


class GmIdentityMatchTests(unittest.TestCase):
    def test_live_discord_names_map_onto_roster(self) -> None:
        racers = [
            (1, ["Blazed"]),
            (2, ["Campbell"]),
            (3, ["Caper"]),
            (4, ["Choog"]),
            (5, ["CmdrRENhoek"]),
            (6, ["Connor"]),
            (7, ["Faarmerryan"]),
            (8, ["Joey"]),
            (9, ["Lilboiloui"]),
            (10, ["MachoMike"]),
            (11, ["Mcdoublehero"]),
            (12, ["Min"]),
            (13, ["Mrmuffn"]),
            (14, ["Nick"]),
            (15, ["Oilempire"]),
            (16, ["Parchie"]),
            (17, ["Randy"]),
            (18, ["Scubasteve"]),
            (19, ["Senpai"]),
            (20, ["Skyvendrake"]),
            (21, ["Starv"]),
            (22, ["Stlrs"]),
            (23, ["Taggart"]),
            (24, ["Thinkblue"]),
            (25, ["Wardo"]),
            (26, ["Wombat"]),
            (27, ["Yammyhotspur"]),
            (28, ["BoiledEgg"]),
            (29, ["Fkncommish"]),
            (30, ["GreedyFish"]),
            (31, ["RJ"]),
        ]
        gms = [
            (1, ["BoiledEgg", "Commish"]),
            (3, ["Joey"]),
            (4, ["Taggart"]),
            (5, ["Stlrs95"]),
            (6, ["Oilempire"]),
            (7, ["MRMUFFN"]),
            (8, ["Nick.CFKV"]),
            (9, ["Farmerryan"]),
            (10, ["pARCHIE5"]),
            (11, ["Random wombat"]),
            (12, ["Connor"]),
            (13, ["ClutchRandy"]),
            (14, ["machomike"]),
            (15, ["thinkblue"]),
            (17, ["Wardo39"]),
            (18, ["Mino71"]),
            (20, ["BlazedBuccaneer"]),
            (22, ["Choog"]),
            (23, ["Skyvendrake"]),
            (24, ["Mark1"]),
            (25, ["CmdrRENhoek"]),
            (27, ["StarV"]),
            (30, ["Lil Boi Loui"]),
            (32, ["DAL- Caper"]),
            (33, ["$enpai"]),
            (34, ["McDoubleHero"]),
            (35, ["YammyHotspur"]),
            (37, ["scubasteved"]),
            (38, ["campbell"]),
            (2, ["BoiledEggDupe"]),
        ]
        chosen = assign_racers_to_gms(racers, gms)
        expected = {
            1: 20,
            2: 38,
            3: 32,
            4: 22,
            5: 25,
            6: 12,
            7: 9,
            8: 3,
            9: 30,
            10: 14,
            11: 34,
            12: 18,
            13: 7,
            14: 8,
            15: 6,
            16: 10,
            17: 13,
            18: 37,
            19: 33,
            20: 23,
            21: 27,
            22: 5,
            23: 4,
            24: 15,
            25: 17,
            26: 11,
            27: 35,
            28: 1,
        }
        for racer_id, user_id in expected.items():
            self.assertEqual(chosen.get(racer_id), user_id, f"racer {racer_id}")
        self.assertNotIn(29, chosen)
        self.assertNotIn(30, chosen)
        self.assertNotIn(31, chosen)
        self.assertNotEqual(chosen.get(28), 2)

    def test_farmerryan_is_close_to_faarmerryan(self) -> None:
        self.assertGreaterEqual(
            identity_match_score(["Faarmerryan"], ["Farmerryan"]),
            60,
        )


if __name__ == "__main__":
    unittest.main()
