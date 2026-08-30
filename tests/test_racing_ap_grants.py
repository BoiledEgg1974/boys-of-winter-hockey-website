"""Granted Formula AP suggestions leave the pending Reward grants list."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from app import create_app
from app.config import Config
from app.league_db import db
from app.racing_models import (
    RacingApSuggestion,
    RacingCircuit,
    RacingEvent,
    RacingEventResult,
    RacingRacer,
)
from app.services.racing_ap import grant_suggestion_batch, pending_suggestions
from app.services.racing_import import refresh_pending_formula_race_ap_suggestions
from app.site_models import ApLedgerEntry


class FormulaApGrantLedgerTests(unittest.TestCase):
    def test_grant_removes_pending_row_and_survives_refresh(self) -> None:
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
                    racer = RacingRacer(
                        display_name="Joey",
                        ap_league_slug="bowl-cap",
                        ap_team_id=29,
                        is_active=True,
                    )
                    circuit = RacingCircuit(name="Formula Circuit", status="active")
                    db.session.add_all([racer, circuit])
                    db.session.flush()
                    event = RacingEvent(
                        circuit_id=int(circuit.id),
                        event_number=1,
                        event_kind="race",
                        title="Thruxton",
                    )
                    db.session.add(event)
                    db.session.flush()
                    db.session.add(
                        RacingEventResult(
                            event_id=int(event.id),
                            racer_id=int(racer.id),
                            position=1,
                            driver_name="Joey",
                            finished=True,
                        )
                    )
                    sug = RacingApSuggestion(
                        scope="race",
                        currency="ap",
                        event_id=int(event.id),
                        racer_id=int(racer.id),
                        driver_key="joey",
                        driver_name="Joey",
                        amount=10,
                        rank=1,
                        status="pending",
                        source_ref=f"formula:event:{event.id}:pos:1:ap",
                    )
                    db.session.add(sug)
                    db.session.commit()

                    stats = grant_suggestion_batch(
                        db.session,
                        [int(sug.id)],
                        destination_league_slug="bowl-cap",
                        created_by_user_id=None,
                        racing_league_slug="bowl-formula",
                    )
                    self.assertEqual(stats["granted"], 1)
                    self.assertEqual(stats["blocked"], 0)
                    self.assertEqual(
                        pending_suggestions(db.session, currency="ap", scope="race"),
                        [],
                    )
                    db.session.refresh(sug)
                    self.assertEqual(sug.status, "granted")
                    ledger = db.session.scalars(select(ApLedgerEntry)).all()
                    self.assertEqual(len(ledger), 1)
                    self.assertEqual(ledger[0].league_slug, "bowl-cap")
                    self.assertEqual(ledger[0].team_id, 29)
                    self.assertEqual(ledger[0].delta, 10)
                    self.assertEqual(ledger[0].source_ref, f"formula:event:{event.id}:pos:1:ap")

                    refresh_pending_formula_race_ap_suggestions(db.session)
                    db.session.commit()
                    self.assertEqual(
                        pending_suggestions(db.session, currency="ap", scope="race"),
                        [],
                    )
                    again = grant_suggestion_batch(
                        db.session,
                        [int(sug.id)],
                        destination_league_slug="bowl-cap",
                        created_by_user_id=None,
                        racing_league_slug="bowl-formula",
                    )
                    self.assertEqual(again["skipped"], 1)
                    self.assertEqual(len(db.session.scalars(select(ApLedgerEntry)).all()), 1)
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()

    def test_grant_uses_racer_mapped_league_when_destination_differs(self) -> None:
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
                    racer = RacingRacer(
                        display_name="BoiledEgg",
                        ap_league_slug="bowl-historical",
                        ap_team_id=9,
                        is_active=True,
                    )
                    db.session.add(racer)
                    db.session.flush()
                    sug = RacingApSuggestion(
                        scope="race",
                        currency="ap",
                        racer_id=int(racer.id),
                        driver_key="boiledegg",
                        driver_name="BoiledEgg",
                        amount=4,
                        rank=7,
                        status="pending",
                        source_ref="formula:event:1:pos:7:ap",
                    )
                    db.session.add(sug)
                    db.session.commit()

                    stats = grant_suggestion_batch(
                        db.session,
                        [int(sug.id)],
                        destination_league_slug="bowl-cap",
                        created_by_user_id=None,
                        racing_league_slug="bowl-formula",
                    )
                    self.assertEqual(stats["granted"], 1)
                    self.assertEqual(pending_suggestions(db.session, currency="ap"), [])
                    row = db.session.scalar(select(ApLedgerEntry))
                    self.assertIsNotNone(row)
                    self.assertEqual(row.league_slug, "bowl-historical")
                    self.assertEqual(row.team_id, 9)
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


if __name__ == "__main__":
    unittest.main()
