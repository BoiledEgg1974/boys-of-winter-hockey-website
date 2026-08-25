"""Formula BOWL CSV import must not keep leftover sample drivers on the homepage."""
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
    RacingChannelCredit,
    RacingCircuit,
    RacingCircuitStanding,
    RacingEvent,
    RacingEventResult,
)
from app.services.racing_csv import (
    classify_export_filename,
    formula_circuit_ap_for_rank,
    formula_circuit_channel_points_for_rank,
    formula_race_ap_for_position,
    select_latest_export_csvs,
)
from app.services.racing_import import (
    import_all_from_raw_dir,
    refresh_pending_formula_circuit_cp_suggestions,
)


SAMPLE_RACE = """race,track,position,number,driver,controller,gear,wear,lap,finished,eliminated,summary
1,Thruxton,1,7,Alice,Alice,4,12,3,true,false,Finished
1,Thruxton,2,3,Bob,Bob,3,8,3,true,false,Finished
1,Thruxton,3,11,Carol,AI,2,5,2,false,true,Out of WP
"""

SAMPLE_STANDINGS = """rank,driver,points,races,wins,best_finish,average_finish
1,Alice,25,1,1,1,1.00
2,Bob,18,1,0,2,2.00
3,Carol,15,1,0,3,3.00
"""

SAMPLE_CP = """rank,driver,channel_points,awards
1,Carol,200,1
2,Bob,0,0
3,Alice,0,0
"""

REAL_RACE = """race,track,position,grid_start,number,driver,controller,gear,wear,lap,finished,eliminated,summary
1,Valencia,1,12,33,Joey,Joey_BOWL,3,10,2,true,false,FINISHED L2
1,Valencia,2,10,67,Randy,ClutchRandy,6,9,2,true,false,FINISHED L2
1,Valencia,3,8,36,Oilempire,OilEmpireGaming,6,8,2,true,false,FINISHED L2
1,Valencia,9,20,6,MachoMike,AI,4,0,1,false,true,OUT (wear)
1,Valencia,10,3,17,Yammyhotspur,AI,4,0,1,false,true,OUT (wear)
"""

REAL_STANDINGS = """rank,driver,points,races,wins,best_finish,average_finish
1,Joey,25,1,1,1,1
2,Randy,18,1,0,2,2
3,Oilempire,15,1,0,3,3
9,MachoMike,0,1,0,9,9
10,Yammyhotspur,0,1,0,10,10
"""

SAMPLE_CREDITS = """rank,viewer,display,channel_credits,awards
1,joey_bowl,Joey,750,1
2,clutchrandy,Randy,250,1
"""


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


class RacingCsvClassifyTests(unittest.TestCase):
    def test_godot_export_filenames_map_to_import_kinds(self) -> None:
        self.assertEqual(
            classify_export_filename("viewer_race_ap_2026-08-22_23-28-12.csv"),
            "viewer_finish_awards",
        )
        self.assertEqual(
            classify_export_filename("viewer_ap_ledger_2026-08-22_23-28-12.csv"),
            "viewer_credit_ledger",
        )
        self.assertEqual(
            classify_export_filename("channel_credits_2026-08-22_23-28-12.csv"),
            "channel_credits",
        )

    def test_formula_race_ap_is_10_down_to_1(self) -> None:
        self.assertEqual(formula_race_ap_for_position(1), 10)
        self.assertEqual(formula_race_ap_for_position(2), 9)
        self.assertEqual(formula_race_ap_for_position(10), 1)
        self.assertEqual(formula_race_ap_for_position(11), 0)
        self.assertEqual(formula_circuit_ap_for_rank(1), 1000)
        self.assertEqual(formula_circuit_ap_for_rank(31), 10)
        self.assertEqual(formula_circuit_ap_for_rank(16), 505)
        self.assertEqual(formula_circuit_ap_for_rank(32), 0)
        self.assertGreater(formula_circuit_ap_for_rank(2), formula_circuit_ap_for_rank(30))

    def test_formula_circuit_channel_points_scale_p11_to_p31(self) -> None:
        self.assertEqual(formula_circuit_channel_points_for_rank(10), 0)
        self.assertEqual(formula_circuit_channel_points_for_rank(11), 3000)
        self.assertEqual(formula_circuit_channel_points_for_rank(12), 2865)
        self.assertEqual(formula_circuit_channel_points_for_rank(31), 300)
        self.assertEqual(formula_circuit_channel_points_for_rank(32), 0)

    def test_select_latest_ignores_older_sample_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            _write(raw / "race_results_2026-08-07_12-00-00.csv", SAMPLE_RACE)
            _write(raw / "race_results_2026-08-22_23-28-12.csv", REAL_RACE)
            _write(raw / "circuit_standings_2026-08-07_12-00-00.csv", SAMPLE_STANDINGS)
            _write(raw / "circuit_standings_2026-08-22_23-28-12.csv", REAL_STANDINGS)
            chosen = {p.name for p in select_latest_export_csvs(raw)}
            self.assertIn("race_results_2026-08-22_23-28-12.csv", chosen)
            self.assertNotIn("race_results_2026-08-07_12-00-00.csv", chosen)
            self.assertNotIn("circuit_standings_2026-08-07_12-00-00.csv", chosen)


class RacingImportLatestWinsTests(unittest.TestCase):
    def test_sample_alice_bob_carol_do_not_survive_real_export(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = root / "raw"
            raw.mkdir()
            _write(raw / "race_results_2026-08-07_12-00-00.csv", SAMPLE_RACE)
            _write(raw / "circuit_standings_2026-08-07_12-00-00.csv", SAMPLE_STANDINGS)
            _write(raw / "channel_points_2026-08-07_12-00-00.csv", SAMPLE_CP)
            _write(raw / "race_results_2026-08-22_23-28-12.csv", REAL_RACE)
            _write(raw / "circuit_standings_2026-08-22_23-28-12.csv", REAL_STANDINGS)
            _write(raw / "channel_credits_2026-08-22_23-28-12.csv", SAMPLE_CREDITS)

            class _TestConfig(Config):
                SQLALCHEMY_DATABASE_URI = f"sqlite:///{(root / 'league.db').as_posix()}"
                SITE_SQLALCHEMY_DATABASE_URI = f"sqlite:///{(root / 'site.db').as_posix()}"
                TESTING = True
                LEAGUE_SLUG = "bowl-formula"
                RAW_IMPORT_DIR = raw
                SQLALCHEMY_BINDS = {}

            app = create_app(_TestConfig)
            with app.app_context():
                try:
                    db.create_all()
                    db.create_all(bind_key="site")
                    import_all_from_raw_dir(db.session, league_slug="bowl-formula")
                    db.session.commit()

                    event = db.session.scalar(select(RacingEvent).limit(1))
                    self.assertIsNotNone(event)
                    assert event is not None
                    self.assertEqual(event.track_name, "Valencia")

                    results = list(
                        db.session.scalars(
                            select(RacingEventResult).order_by(RacingEventResult.position.asc())
                        ).all()
                    )
                    names = [r.driver_name for r in results]
                    self.assertEqual(names[:3], ["Joey", "Randy", "Oilempire"])
                    self.assertNotIn("Alice", names)
                    self.assertNotIn("Bob", names)
                    self.assertNotIn("Carol", names)

                    standings = list(
                        db.session.scalars(
                            select(RacingCircuitStanding).order_by(RacingCircuitStanding.rank.asc())
                        ).all()
                    )
                    standing_by_name = {s.driver_name: s for s in standings}
                    self.assertNotIn("Alice", standing_by_name)
                    self.assertNotIn("Carol", standing_by_name)
                    self.assertEqual(standing_by_name["Joey"].points, 25)
                    self.assertEqual(standing_by_name["MachoMike"].points, 2)
                    self.assertEqual(standing_by_name["Yammyhotspur"].points, 1)
                    self.assertEqual(standing_by_name["Joey"].action_points, 1000)
                    self.assertEqual(standing_by_name["MachoMike"].rank, 4)
                    self.assertEqual(standing_by_name["Yammyhotspur"].rank, 5)
                    self.assertEqual(standing_by_name["Joey"].channel_points, 0)

                    from app.racing_models import RacingApSuggestion

                    race_ap = list(
                        db.session.scalars(
                            select(RacingApSuggestion).where(
                                RacingApSuggestion.scope == "race",
                                RacingApSuggestion.currency == "ap",
                            )
                        ).all()
                    )
                    by_rank = {int(s.rank): s for s in race_ap if s.rank}
                    self.assertEqual(by_rank[1].amount, 10)
                    self.assertEqual(by_rank[1].driver_name, "Joey")
                    self.assertEqual(by_rank[2].amount, 9)
                    self.assertEqual(by_rank[3].amount, 8)
                    self.assertEqual(by_rank[9].amount, 2)
                    self.assertEqual(by_rank[9].driver_name, "MachoMike")
                    self.assertEqual(by_rank[10].amount, 1)
                    self.assertEqual(by_rank[10].driver_name, "Yammyhotspur")

                    credits = {
                        c.login: c
                        for c in db.session.scalars(select(RacingChannelCredit)).all()
                    }
                    self.assertEqual(credits["joey_bowl"].channel_credits, 750)
                    self.assertEqual(credits["clutchrandy"].channel_credits, 250)
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


class FormulaCircuitCpGrantTests(unittest.TestCase):
    def test_circuit_cp_grants_are_p11_to_p31_not_top_five(self) -> None:
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
                    circuit = RacingCircuit(name="Formula Circuit", status="active")
                    db.session.add(circuit)
                    db.session.flush()
                    db.session.add_all(
                        [
                            RacingCircuitStanding(
                                circuit_id=int(circuit.id),
                                driver_key="joey",
                                driver_name="Joey",
                                rank=1,
                                points=25,
                                channel_points=1000,
                            ),
                            RacingCircuitStanding(
                                circuit_id=int(circuit.id),
                                driver_key="mcdoublehero",
                                driver_name="Mcdoublehero",
                                rank=11,
                                points=0,
                                channel_points=0,
                            ),
                        ]
                    )
                    db.session.add(
                        RacingApSuggestion(
                            scope="circuit",
                            currency="channel_points",
                            circuit_id=int(circuit.id),
                            driver_key="joey",
                            driver_name="Joey",
                            amount=1000,
                            rank=1,
                            status="pending",
                            source_ref="circuit:1:rank:1:cp",
                        )
                    )
                    db.session.commit()
                    refresh_pending_formula_circuit_cp_suggestions(db.session)
                    db.session.commit()
                    rows = list(
                        db.session.scalars(
                            select(RacingApSuggestion).where(
                                RacingApSuggestion.scope == "circuit",
                                RacingApSuggestion.currency == "channel_points",
                                RacingApSuggestion.status == "pending",
                            )
                        ).all()
                    )
                    by_name = {r.driver_name: r for r in rows}
                    self.assertNotIn("Joey", by_name)
                    self.assertEqual(by_name["Mcdoublehero"].rank, 11)
                    self.assertEqual(by_name["Mcdoublehero"].amount, 3000)
                finally:
                    db.session.remove()
                    db.drop_all()
                    db.drop_all(bind_key="site")
                    for engine in db.engines.values():
                        engine.dispose()


if __name__ == "__main__":
    unittest.main()
