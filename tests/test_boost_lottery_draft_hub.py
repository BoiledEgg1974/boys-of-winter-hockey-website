"""Draft Hub boost lottery pool, draw apply, and go-live integration."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

from app import create_app
from app.config import Config
from app.db_utils import (
    ensure_boost_lottery_scratch_extras_sqlite,
    ensure_boost_lottery_team_results_sqlite,
    ensure_league_draft_boost_pool_sqlite,
    ensure_league_draft_slot_boost_tier_sqlite,
)
from app.league_db import db
from app.models import Team
from app.services.boost_lottery import (
    apply_boost_draw,
    build_pool,
    execute_draw,
    generate_pool_for_draft,
    validate_ranges,
)
from app.site_models import (
    BoostLotteryTeamResult,
    GmLeagueMembership,
    LeagueDraft,
    LeagueDraftSlot,
    User,
)


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


class BoostLotteryServiceTests(unittest.TestCase):
    def test_build_pool_matches_js_semantics(self) -> None:
        pool = build_pool(28, 31, 82, 84)
        self.assertEqual(pool.count(28), 3)
        self.assertEqual(pool.count(29), 3)
        self.assertEqual(pool.count(30), 3)
        self.assertEqual(pool.count(82), 1)
        self.assertEqual(pool.count(83), 1)
        self.assertEqual(len(pool), 11)

    def test_validate_ranges(self) -> None:
        self.assertIsNone(validate_ranges(28, 81, 82, 216))
        self.assertIsNotNone(validate_ranges(81, 28, 82, 216))

    def test_execute_draw_returns_unique_winners(self) -> None:
        pool = build_pool(1, 4, 10, 12)
        rng = _SeqRng([0.0, 0.0, 0.0, 0.0])
        result = execute_draw(pool, 2, 1, rng=rng)
        self.assertNotIsInstance(result, str)
        gold, silver, remaining = result
        self.assertEqual(len(gold), 2)
        self.assertEqual(len(silver), 1)
        self.assertEqual(len(set(gold + silver)), 3)
        self.assertTrue(all(n not in gold + silver for n in remaining))


class BoostLotteryDraftHubIntegrationTests(unittest.TestCase):
    def _app(self, tmp: str):
        root = Path(tmp)
        league_db = root / "league.db"
        site_db = root / "site.db"

        class _TestConfig(Config):
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{league_db.as_posix()}"
            SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{site_db.as_posix()}"
            TESTING = True
            LEAGUE_SLUG = "bowl-cap"
            SQLALCHEMY_BINDS = {}
            WTF_CSRF_ENABLED = False

        return create_app(_TestConfig)

    def test_draw_applies_slots_and_tracker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            with app.app_context():
                try:
                    db.create_all()
                    db.create_all(bind_key="site")
                    site_engine = db.engines["site"]
                    ensure_league_draft_slot_boost_tier_sqlite(site_engine)
                    ensure_boost_lottery_team_results_sqlite(site_engine)
                    ensure_league_draft_boost_pool_sqlite(site_engine)

                    team_a = Team(name="Alpha", abbreviation="ALP", slug="alpha")
                    team_b = Team(name="Beta", abbreviation="BET", slug="beta")
                    db.session.add_all([team_a, team_b])
                    db.session.flush()

                    draft = LeagueDraft(
                        league_slug="bowl-cap",
                        name="Test Draft",
                        status="setup",
                        timeline_year=2030,
                        min_age_years=18,
                        min_anchor_month=9,
                        min_anchor_day=30,
                        max_age_years=20,
                        max_anchor_month=9,
                        max_anchor_day=30,
                    )
                    db.session.add(draft)
                    db.session.flush()

                    db.session.add_all(
                        [
                            LeagueDraftSlot(
                                league_draft_id=draft.id,
                                overall_pick=1,
                                round=1,
                                team_id=int(team_a.id),
                                original_team_id=int(team_a.id),
                            ),
                            LeagueDraftSlot(
                                league_draft_id=draft.id,
                                overall_pick=2,
                                round=1,
                                team_id=int(team_b.id),
                                original_team_id=int(team_b.id),
                            ),
                        ]
                    )
                    db.session.commit()

                    payload, err = apply_boost_draw(
                        db.session,
                        draft,
                        "bowl-cap",
                        gold_picks=[1],
                        silver_picks=[2],
                        user_id=1,
                    )
                    self.assertIsNone(err)
                    self.assertEqual(payload["applied_gold"], 1)
                    self.assertEqual(payload["applied_silver"], 1)

                    slots = list(
                        db.session.scalars(
                            select(LeagueDraftSlot).where(LeagueDraftSlot.league_draft_id == draft.id)
                        ).all()
                    )
                    by_overall = {int(s.overall_pick): s for s in slots}
                    self.assertEqual(by_overall[1].boost_tier, "gold")
                    self.assertEqual(by_overall[2].boost_tier, "silver")

                    rows = list(
                        db.session.scalars(
                            select(BoostLotteryTeamResult).where(
                                BoostLotteryTeamResult.league_slug == "bowl-cap"
                            )
                        ).all()
                    )
                    by_team = {int(r.team_id): r for r in rows}
                    self.assertEqual(by_team[int(team_a.id)].gold_count, 1)
                    self.assertEqual(by_team[int(team_b.id)].silver_count, 1)
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()

    def test_boost_lottery_redirect_and_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            with app.app_context():
                try:
                    db.create_all()
                    db.create_all(bind_key="site")
                    site_engine = db.engines["site"]
                    ensure_league_draft_slot_boost_tier_sqlite(site_engine)
                    ensure_boost_lottery_team_results_sqlite(site_engine)
                    ensure_league_draft_boost_pool_sqlite(site_engine)
                    ensure_boost_lottery_scratch_extras_sqlite(site_engine)

                    admin = User(
                        email="bl-admin@example.invalid",
                        password_hash="x",
                        discord_name="Admin",
                        is_admin=True,
                        admin_role="league_admin",
                    )
                    gm = User(
                        email="bl-gm@example.invalid",
                        password_hash="x",
                        discord_name="GM",
                        is_admin=False,
                        admin_role=None,
                    )
                    db.session.add_all([admin, gm])
                    db.session.flush()
                    db.session.add(
                        GmLeagueMembership(
                            league_slug="bowl-cap",
                            user_id=int(gm.id),
                            team_id=1,
                            status="active",
                        )
                    )
                    draft = LeagueDraft(
                        league_slug="bowl-cap",
                        name="Cap Draft",
                        status="setup",
                        timeline_year=2030,
                        min_age_years=18,
                        min_anchor_month=9,
                        min_anchor_day=30,
                        max_age_years=20,
                        max_anchor_month=9,
                        max_anchor_day=30,
                    )
                    db.session.add(draft)
                    db.session.flush()
                    db.session.add(
                        LeagueDraftSlot(
                            league_draft_id=draft.id,
                            overall_pick=10,
                            round=1,
                            team_id=1,
                            original_team_id=1,
                        )
                    )
                    db.session.commit()
                    admin_id = int(admin.id)
                    gm_id = int(gm.id)

                    with app.test_client() as client:
                        _login(client, admin_id)
                        redirect = client.get("/boost-lottery")
                        self.assertEqual(redirect.status_code, 302)
                        self.assertIn("/boost-lottery-tracker", redirect.headers.get("Location", ""))

                        gen = client.post(
                            "/draft-hub/admin/boost-lottery/generate-pool",
                            json={"triple_lo": 1, "triple_hi": 30, "single_lo": 30, "single_hi": 40},
                        )
                        self.assertEqual(gen.status_code, 200, gen.get_data(as_text=True))
                        self.assertTrue(gen.get_json()["boost_lottery"]["pool_ready"])

                        draw = client.post("/draft-hub/admin/boost-lottery/execute-draw", json={})
                        self.assertEqual(draw.status_code, 400)
                        self.assertIn("matching slot", draw.get_json()["error"].lower())

                        _login(client, gm_id)
                        tracker = client.get("/boost-lottery-tracker")
                        self.assertEqual(tracker.status_code, 200)
                        self.assertIn("Boost Lottery Tracker", tracker.get_data(as_text=True))
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


class DraftHubBoostPracticeAccessTests(unittest.TestCase):
    def _app(self, tmp: str):
        class _TestConfig(Config):
            TESTING = True
            WTF_CSRF_ENABLED = False
            SECRET_KEY = "test"
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{Path(tmp) / 'league.db'}"
            SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{Path(tmp) / 'site.db'}"
            LEAGUE_SLUG = "bowl-cap"
            SQLALCHEMY_BINDS = {}

        return create_app(_TestConfig)

    def _seed_setup_draft(self) -> None:
        draft = LeagueDraft(
            league_slug="bowl-cap",
            name="Cap Draft",
            status="setup",
            timeline_year=2030,
            min_age_years=18,
            min_anchor_month=9,
            min_anchor_day=30,
            max_age_years=20,
            max_anchor_month=9,
            max_anchor_day=30,
        )
        db.session.add(draft)
        db.session.commit()

    def _teardown_db(self) -> None:
        db.session.remove()
        db.drop_all()
        db.drop_all(bind_key="site")
        for engine in db.engines.values():
            engine.dispose()

    def test_anonymous_draft_hub_has_no_scratch_board(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            with app.app_context():
                db.create_all()
                db.create_all(bind_key="site")
                self._seed_setup_draft()
            with app.test_client() as client:
                page = client.get("/draft-hub")
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertNotIn("dh-boost-scratch-host", html)
            self.assertNotIn("data-boost-scratch", html)
            with app.app_context():
                self._teardown_db()

    def test_gm_gets_practice_scratch_not_live_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            with app.app_context():
                db.create_all()
                db.create_all(bind_key="site")
                gm = User(
                    email="cap-gm@example.invalid",
                    password_hash="x",
                    discord_name="GM",
                    is_admin=False,
                    admin_role=None,
                )
                db.session.add(gm)
                db.session.flush()
                db.session.add(
                    GmLeagueMembership(
                        league_slug="bowl-cap",
                        user_id=int(gm.id),
                        team_id=1,
                        status="active",
                    )
                )
                self._seed_setup_draft()
                gm_id = int(gm.id)
            with app.test_client() as client:
                _login(client, gm_id)
                page = client.get("/draft-hub")
            html = page.get_data(as_text=True)
            self.assertIn("dh-boost-scratch-host", html)
            self.assertIn('data-role="gm"', html)
            self.assertIn("Practice — does not affect the lottery", html)
            self.assertNotIn('id="bs-mode-live"', html)
            with app.app_context():
                self._teardown_db()

    def test_staff_setup_gets_live_scratch_controls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            app = self._app(tmp)
            with app.app_context():
                db.create_all()
                db.create_all(bind_key="site")
                admin = User(
                    email="cap-admin@example.invalid",
                    password_hash="x",
                    discord_name="Admin",
                    is_admin=True,
                    admin_role="league_admin",
                )
                db.session.add(admin)
                db.session.flush()
                self._seed_setup_draft()
                admin_id = int(admin.id)
            with app.test_client() as client:
                _login(client, admin_id)
                page = client.get("/draft-hub")
            html = page.get_data(as_text=True)
            self.assertIn('data-role="admin"', html)
            self.assertIn('id="bs-mode-live"', html)
            with app.app_context():
                self._teardown_db()


if __name__ == "__main__":
    unittest.main()
