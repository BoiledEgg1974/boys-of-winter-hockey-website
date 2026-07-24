"""All-stars CSV import must never wipe existing League History teams (all three leagues)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from app import create_app
from app.config import Config, league_slugs
from app.league_db import db
from app.models import HistoryAllStar, Player, Season
from app.services.admin_history_records import HISTORY_SOURCE_ADMIN, HISTORY_SOURCE_CSV
from scripts.import_pipeline.runner import import_history_all_stars


class AllStarsWipeProtectionTests(unittest.TestCase):
    def _run_for_each_league(self, body) -> None:
        """Exercise the shared importer under each mounted league's app context."""
        for slug in league_slugs():
            with self.subTest(league=slug):
                with tempfile.TemporaryDirectory() as tmp:
                    db_path = Path(tmp) / f"{slug}.db"

                    class _TestConfig(Config):
                        SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path.as_posix()}"
                        TESTING = True
                        LEAGUE_SLUG = slug

                    app = create_app(_TestConfig)
                    with app.app_context():
                        try:
                            db.create_all()
                            body(app, Path(tmp), slug)
                        finally:
                            db.session.remove()
                            db.drop_all()
                            for engine in db.engines.values():
                                engine.dispose()

    def test_incomplete_csv_does_not_wipe_existing_slots_on_all_leagues(self) -> None:
        def body(app, tmp: Path, slug: str) -> None:
            season = Season(label="1998-99", start_year=1998, end_year=1999, is_current=False)
            csv_player = Player(
                first_name="Marty",
                last_name="Turco",
                full_name="Marty Turco",
                position="G",
                fhm_player_id="101",
            )
            admin_player = Player(
                first_name="Trevor",
                last_name="Kidd",
                full_name="Trevor Kidd",
                position="G",
                fhm_player_id="202",
            )
            incoming = Player(
                first_name="Joe",
                last_name="Sakic",
                full_name="Joe Sakic",
                position="C",
                fhm_player_id="303",
            )
            db.session.add_all([season, csv_player, admin_player, incoming])
            db.session.flush()
            db.session.add_all(
                [
                    HistoryAllStar(
                        season_id=int(season.id),
                        season_label="1998-99",
                        team_rank=1,
                        slot=1,
                        position="Goal",
                        player_id=int(csv_player.id),
                        source=HISTORY_SOURCE_CSV,
                    ),
                    HistoryAllStar(
                        season_id=int(season.id),
                        season_label="1998-99",
                        team_rank=2,
                        slot=1,
                        position="Goal",
                        player_id=int(admin_player.id),
                        source=HISTORY_SOURCE_ADMIN,
                    ),
                ]
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            # Incomplete CSV: omits the existing First Team goalie, tries to overwrite admin
            # Second Team goalie, and adds a new First Team center.
            (raw / "history_all_stars.csv").write_text(
                "season,team,slot,position,player_id,team_id,notes\n"
                "1998-99,2,1,Goal,202,-1,\n"
                "1998-99,1,5,Center,303,-1,\n",
                encoding="utf-8",
            )

            upserted = import_history_all_stars(raw, app)
            self.assertEqual(upserted, 1, slug)

            rows = list(db.session.scalars(select(HistoryAllStar)).all())
            by_key = {(r.season_label, int(r.team_rank), int(r.slot)): r for r in rows}
            self.assertEqual(len(rows), 3, slug)

            kept_csv = by_key[("1998-99", 1, 1)]
            self.assertEqual(kept_csv.player_id, int(csv_player.id))
            self.assertEqual(kept_csv.source, HISTORY_SOURCE_CSV)

            kept_admin = by_key[("1998-99", 2, 1)]
            self.assertEqual(kept_admin.player_id, int(admin_player.id))
            self.assertEqual(kept_admin.source, HISTORY_SOURCE_ADMIN)

            added = by_key[("1998-99", 1, 5)]
            self.assertEqual(added.player_id, int(incoming.id))
            self.assertEqual(added.position, "Center")
            self.assertEqual(added.source, HISTORY_SOURCE_CSV)

        self._run_for_each_league(body)

    def test_csv_updates_existing_csv_row_in_place_on_all_leagues(self) -> None:
        def body(app, tmp: Path, slug: str) -> None:
            season = Season(label="1998-99", start_year=1998, end_year=1999, is_current=False)
            old_player = Player(
                first_name="Old",
                last_name="Goalie",
                full_name="Old Goalie",
                position="G",
                fhm_player_id="11",
            )
            new_player = Player(
                first_name="New",
                last_name="Goalie",
                full_name="New Goalie",
                position="G",
                fhm_player_id="22",
            )
            db.session.add_all([season, old_player, new_player])
            db.session.flush()
            db.session.add(
                HistoryAllStar(
                    season_id=int(season.id),
                    season_label="1998-99",
                    team_rank=1,
                    slot=1,
                    position="Goal",
                    player_id=int(old_player.id),
                    source=HISTORY_SOURCE_CSV,
                )
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            (raw / "history_all_stars.csv").write_text(
                "season,team,slot,position,player_id,team_id,notes\n"
                "1998-99,1,1,Goalie,22,-1,\n",
                encoding="utf-8",
            )

            upserted = import_history_all_stars(raw, app)
            self.assertEqual(upserted, 1, slug)
            self.assertEqual(
                db.session.scalar(select(func.count()).select_from(HistoryAllStar)) or 0,
                1,
            )
            row = db.session.scalar(select(HistoryAllStar).limit(1))
            assert row is not None
            self.assertEqual(row.player_id, int(new_player.id))
            self.assertEqual(row.position, "Goalie")
            self.assertEqual(row.source, HISTORY_SOURCE_CSV)

        self._run_for_each_league(body)

    def test_empty_csv_file_does_not_wipe_on_all_leagues(self) -> None:
        """Header-only / empty CSVs must not clear existing all-star teams."""

        def body(app, tmp: Path, slug: str) -> None:
            season = Season(label="1998-99", start_year=1998, end_year=1999, is_current=False)
            player = Player(
                first_name="Keep",
                last_name="Me",
                full_name="Keep Me",
                position="G",
                fhm_player_id="4242",
            )
            db.session.add_all([season, player])
            db.session.flush()
            db.session.add(
                HistoryAllStar(
                    season_id=int(season.id),
                    season_label="1998-99",
                    team_rank=1,
                    slot=1,
                    position="Goal",
                    player_id=int(player.id),
                    source=HISTORY_SOURCE_CSV,
                )
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            (raw / "history_all_stars.csv").write_text(
                "season,team,slot,position,player_id,team_id,notes\n",
                encoding="utf-8",
            )

            upserted = import_history_all_stars(raw, app)
            self.assertEqual(upserted, 0, slug)
            row = db.session.scalar(select(HistoryAllStar).limit(1))
            assert row is not None
            self.assertEqual(row.player_id, int(player.id))
            self.assertEqual(row.source, HISTORY_SOURCE_CSV)

        self._run_for_each_league(body)


if __name__ == "__main__":
    unittest.main()
