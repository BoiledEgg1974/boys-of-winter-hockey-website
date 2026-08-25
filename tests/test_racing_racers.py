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
from app.services.racing_racers import delete_racer, ensure_alias, link_roster_txt


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


if __name__ == "__main__":
    unittest.main()
