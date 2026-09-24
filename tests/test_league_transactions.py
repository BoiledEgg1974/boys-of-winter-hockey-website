"""League transaction feed (CSV + roster import deltas)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select

from app import create_app
from app.config import make_league_config
from app.models import LeagueTransaction, Team, db
from app.services.league_transactions import (
    import_transactions_csv,
    record_roster_moves_from_import,
    transaction_kind_label,
)
from app.site_models import RosterImportSnapshot


def _isolated_bowl_fantasy_app(tmp: Path):
    base_cfg = make_league_config("bowl-fantasy")
    league_db = tmp / "league.db"
    site_db = tmp / "site.db"

    class IsolatedConfig(base_cfg):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{league_db.as_posix()}"
        SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{site_db.as_posix()}"

    app = create_app(IsolatedConfig)
    app.config["TESTING"] = True
    return app


def _dispose_db(app) -> None:
    with app.app_context():
        db.session.remove()
        db.engine.dispose()
        db.get_engine(app, bind="site").dispose()


class LeagueTransactionHelpersTests(unittest.TestCase):
    def test_kind_labels(self) -> None:
        self.assertEqual(transaction_kind_label("waiver"), "Waiver")
        self.assertEqual(transaction_kind_label("roster_move"), "Roster move")


class RosterDeltaImportTests(unittest.TestCase):
    def test_second_import_creates_roster_move(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            app = _isolated_bowl_fantasy_app(tmp)
            raw = tmp / "raw"
            raw.mkdir()
            (raw / "player_master.csv").write_text(
                "PlayerId;TeamId;First Name;Last Name\n"
                "1;10;Alex;Forward\n"
                "2;11;Bob;Defense\n",
                encoding="utf-8",
            )
            with app.app_context():
                db.create_all()
                db.create_all(bind_key="site")
                t10 = Team(
                    fhm_team_id="10",
                    slug="aaa-t10",
                    abbreviation="AAA",
                    name="Alpha",
                    fhm_league_id=0,
                    fhm_conference_id=0,
                )
                t11 = Team(
                    fhm_team_id="11",
                    slug="bbb-t11",
                    abbreviation="BBB",
                    name="Beta",
                    fhm_league_id=0,
                    fhm_conference_id=1,
                )
                db.session.add_all([t10, t11])
                db.session.commit()
                snap = RosterImportSnapshot(
                    league_slug="bowl-fantasy",
                    snapshot_json=json.dumps({"1": 10, "2": 11}),
                )
                db.session.add(snap)
                db.session.commit()

                (raw / "player_master.csv").write_text(
                    "PlayerId;TeamId;First Name;Last Name\n"
                    "1;11;Alex;Forward\n"
                    "2;11;Bob;Defense\n",
                    encoding="utf-8",
                )
                n = record_roster_moves_from_import(raw, app)
                self.assertEqual(n, 1)
                rows = list(db.session.scalars(select(LeagueTransaction)))
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].kind, "roster_move")
                self.assertIn("Alex", rows[0].headline)
            _dispose_db(app)


class TransactionsCsvImportTests(unittest.TestCase):
    def test_import_transactions_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            app = _isolated_bowl_fantasy_app(tmp)
            raw = tmp / "raw"
            raw.mkdir()
            (raw / "transactions.csv").write_text(
                "date;type;team;headline;body;external_id\n"
                "2025-10-1;waiver;AAA;Test claim;Claimed;tx-1\n",
                encoding="utf-8",
            )
            with app.app_context():
                db.create_all()
                db.session.add(
                    Team(
                        fhm_team_id="10",
                        slug="aaa-t10",
                        abbreviation="AAA",
                        name="Alpha",
                        fhm_league_id=0,
                    )
                )
                db.session.commit()
                count = import_transactions_csv(raw, app)
                self.assertEqual(count, 1)
                row = db.session.scalars(select(LeagueTransaction).limit(1)).first()
                self.assertIsNotNone(row)
                assert row is not None
                self.assertEqual(row.headline, "Test claim")
                self.assertEqual(row.kind, "waiver")
            _dispose_db(app)


if __name__ == "__main__":
    unittest.main()
