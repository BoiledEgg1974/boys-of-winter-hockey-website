"""Scratch-ticket extras odds, persistence, and admin/GM access."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app import create_app
from app.config import Config
from app.db_utils import ensure_boost_lottery_scratch_extras_sqlite
from app.league_db import db
from app.services.boost_scratch import (
    draw_totals,
    extras_payload,
    load_scratch_extras,
    normalize_ticket_summary,
    plus_two_rate,
    reset_scratch_extras,
    roll_session,
    roll_ticket,
    save_scratch_extras,
    tally_extras,
)
from app.site_models import BoostLotteryScratchExtras, GmLeagueMembership, User


class _SeqRng:
    def __init__(self, values: list[float]) -> None:
        self.values = list(values)
        self.i = 0

    def random(self) -> float:
        if self.i >= len(self.values):
            raise AssertionError("random() called more times than scripted")
        value = self.values[self.i]
        self.i += 1
        return value


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess.clear()
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


class BoostScratchOddsTests(unittest.TestCase):
    def test_plus_two_schedule(self) -> None:
        self.assertAlmostEqual(plus_two_rate(0), 0.15)
        self.assertAlmostEqual(plus_two_rate(1), 0.10)
        self.assertAlmostEqual(plus_two_rate(2), 0.05)
        self.assertAlmostEqual(plus_two_rate(9), 0.05)

    def test_nothing_plus_two_respins_to_gold_and_appends_ticket(self) -> None:
        rng = _SeqRng(
            [
                0.90,  # ticket 0 prize: nothing
                0.01,  # ticket 0 +2 hit
                0.10,  # re-spin gold
                0.40,  # ticket 1 prize: silver
                0.99,  # ticket 1 no +2
                0.90,  # ticket 2 prize: nothing
                0.99,  # ticket 2 no +2
                0.10,  # bonus ticket prize: gold
                0.99,  # bonus no +2
            ]
        )
        tickets = roll_session(rng)
        self.assertEqual(len(tickets), 4)
        self.assertEqual(tickets[0]["prize"], "gold")
        self.assertTrue(tickets[0]["plus_two"])
        self.assertEqual(tickets[1]["prize"], "silver")
        self.assertEqual(tickets[2]["prize"], "nothing")
        self.assertEqual(tickets[3]["prize"], "gold")
        self.assertEqual(tally_extras(tickets), (2, 1))

    def test_roll_ticket_keeps_gold_on_plus_two(self) -> None:
        ticket = roll_ticket(0, _SeqRng([0.10, 0.01]))
        self.assertEqual(ticket["prize"], "gold")
        self.assertTrue(ticket["plus_two"])

    def test_draw_totals_stack_on_baseline(self) -> None:
        self.assertEqual(draw_totals(4, 6, 2, 1), (6, 7))
        self.assertEqual(draw_totals(4, 6, 0, 0), (4, 6))

    def test_normalize_ticket_summary(self) -> None:
        tickets = normalize_ticket_summary(
            '[{"prize":"GOLD","plus_two":1},{"prize":"ghost"},{"prize":"silver"}]'
        )
        self.assertEqual(
            tickets,
            [{"prize": "gold", "plus_two": True}, {"prize": "silver", "plus_two": False}],
        )


class BoostScratchPersistTests(unittest.TestCase):
    def test_save_rehydrate_and_reset(self) -> None:
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
                WTF_CSRF_ENABLED = False

            app = create_app(_TestConfig)
            with app.app_context():
                try:
                    db.create_all()
                    db.create_all(bind_key="site")
                    ensure_boost_lottery_scratch_extras_sqlite(db.engines["site"])
                    tickets = [
                        {"prize": "gold", "plus_two": True},
                        {"prize": "silver", "plus_two": False},
                        {"prize": "nothing", "plus_two": False},
                        {"prize": "gold", "plus_two": False},
                    ]
                    saved = save_scratch_extras(
                        db.session, "bowl-fantasy", tickets=tickets, user_id=9
                    )
                    db.session.commit()
                    self.assertEqual(saved["extra_gold"], 2)
                    self.assertEqual(saved["extra_silver"], 1)
                    self.assertTrue(saved["complete"])

                    loaded = load_scratch_extras(db.session, "bowl-fantasy")
                    self.assertEqual(loaded["extra_gold"], 2)
                    self.assertEqual(loaded["extra_silver"], 1)
                    self.assertEqual(len(loaded["tickets"]), 4)

                    row = db.session.scalars(select(BoostLotteryScratchExtras)).first()
                    self.assertIsNotNone(row)
                    assert row is not None
                    self.assertEqual(row.updated_by_user_id, 9)
                    payload = extras_payload(row)
                    self.assertEqual(payload["extra_gold"], 2)

                    reset = reset_scratch_extras(db.session, "bowl-fantasy", user_id=9)
                    db.session.commit()
                    self.assertEqual(reset["extra_gold"], 0)
                    self.assertEqual(reset["extra_silver"], 0)
                    self.assertFalse(reset["complete"])
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


class BoostScratchRouteTests(unittest.TestCase):
    def _app(self, tmp: str):
        root = Path(tmp)
        league_db = root / "league.db"
        site_db = root / "site.db"

        class _TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{league_db.as_posix()}"
            SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{site_db.as_posix()}"
            TESTING = True
            LEAGUE_SLUG = "bowl-fantasy"
            SQLALCHEMY_BINDS = {}
            WTF_CSRF_ENABLED = False

        return create_app(_TestConfig)

    def test_admin_saves_and_gm_cannot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            with app.app_context():
                try:
                    db.create_all()
                    db.create_all(bind_key="site")
                    ensure_boost_lottery_scratch_extras_sqlite(db.engines["site"])
                    admin = User(
                        email="scratch-admin@example.invalid",
                        password_hash="x",
                        discord_name="Admin",
                        is_admin=True,
                        admin_role="league_admin",
                    )
                    gm = User(
                        email="scratch-gm@example.invalid",
                        password_hash="x",
                        discord_name="GM",
                        is_admin=False,
                        admin_role=None,
                    )
                    db.session.add_all([admin, gm])
                    db.session.flush()
                    db.session.add(
                        GmLeagueMembership(
                            league_slug="bowl-fantasy",
                            user_id=int(gm.id),
                            team_id=1,
                            status="active",
                        )
                    )
                    db.session.commit()
                    admin_id = int(admin.id)
                    gm_id = int(gm.id)
                    self.assertFalse(bool(gm.is_admin))
                    self.assertNotEqual(admin_id, gm_id)

                    with app.test_client() as client:
                        _login(client, admin_id)
                        page = client.get("/boost-lottery", follow_redirects=True)
                        self.assertEqual(page.status_code, 200)
                        html = page.get_data(as_text=True)
                        self.assertIn("Boost Lottery Tracker", html)
                        self.assertNotIn("Scratch tickets", html)
                        self.assertNotIn("Practice — does not affect the lottery", html)

                        save = client.post(
                            "/boost-lottery/scratch-extras",
                            json={
                                "tickets": [
                                    {"prize": "gold", "plus_two": False},
                                    {"prize": "gold", "plus_two": False},
                                    {"prize": "silver", "plus_two": False},
                                ]
                            },
                        )
                        self.assertEqual(save.status_code, 200, save.get_data(as_text=True))
                        body = save.get_json()
                        self.assertEqual(body["extra_gold"], 2)
                        self.assertEqual(body["extra_silver"], 1)

                        page2 = client.get("/boost-lottery-tracker")
                        html2 = page2.get_data(as_text=True)
                        self.assertIn("Boost winner tracker", html2)

                        fake_gm = SimpleNamespace(
                            is_authenticated=True,
                            is_active=True,
                            is_anonymous=False,
                            is_admin=False,
                            id=gm_id,
                            get_id=lambda: str(gm_id),
                        )
                        with patch("flask_login.utils._get_user", return_value=fake_gm):
                            denied = client.post(
                                "/boost-lottery/scratch-extras",
                                json={"tickets": [{"prize": "gold", "plus_two": True}]},
                            )
                        self.assertEqual(denied.status_code, 403, denied.get_data(as_text=True))

                        _login(client, admin_id)
                        reset = client.post("/boost-lottery/scratch-extras/reset", json={})
                        self.assertEqual(reset.status_code, 200)
                        self.assertEqual(reset.get_json()["extra_gold"], 0)
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


if __name__ == "__main__":
    unittest.main()
