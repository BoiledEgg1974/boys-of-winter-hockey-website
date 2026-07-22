"""Hall of Fame CSV import must never wipe existing inductees (all three leagues)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from app import create_app
from app.config import Config, league_slugs
from app.league_db import db
from app.models import HallOfFameMember, Player
from app.services.admin_hall_of_fame import HOF_SOURCE_ADMIN, HOF_SOURCE_CSV
from scripts.import_pipeline.runner import import_hall_of_fame


class HallOfFameWipeProtectionTests(unittest.TestCase):
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

    def test_incomplete_csv_does_not_wipe_existing_inductees_on_all_leagues(self) -> None:
        def body(app, tmp: Path, slug: str) -> None:
            csv_only = Player(
                first_name="Allan",
                last_name="Stanley",
                full_name="Allan Stanley",
                position="D",
                fhm_player_id="507",
            )
            admin_only = Player(
                first_name="Sid",
                last_name="Abel",
                full_name="Sid Abel",
                position="C",
                fhm_player_id="9001",
            )
            incoming = Player(
                first_name="Johnny",
                last_name="Bower",
                full_name="Johnny Bower",
                position="G",
                fhm_player_id="61",
            )
            db.session.add_all([csv_only, admin_only, incoming])
            db.session.flush()
            db.session.add_all(
                [
                    HallOfFameMember(
                        player_id=int(csv_only.id),
                        member_kind="skater",
                        inducted_year=1968,
                        sort_order=1,
                        source=HOF_SOURCE_CSV,
                    ),
                    HallOfFameMember(
                        player_id=int(admin_only.id),
                        member_kind="skater",
                        inducted_year=1969,
                        sort_order=0,
                        source=HOF_SOURCE_ADMIN,
                    ),
                ]
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            (raw / "hall_of_fame.csv").write_text(
                "fhm_player_id,kind,inducted_year,sort_order\n"
                "61,goalie,1967,0\n"
                "9001,skater,1950,99\n",
                encoding="utf-8",
            )

            upserted = import_hall_of_fame(raw, app)
            self.assertEqual(upserted, 1, slug)

            rows = list(db.session.scalars(select(HallOfFameMember)).all())
            by_player = {int(r.player_id): r for r in rows}
            self.assertEqual(len(rows), 3, slug)
            self.assertEqual(by_player[int(csv_only.id)].inducted_year, 1968)
            self.assertEqual(by_player[int(csv_only.id)].source, HOF_SOURCE_CSV)
            self.assertEqual(by_player[int(admin_only.id)].inducted_year, 1969)
            self.assertEqual(by_player[int(admin_only.id)].source, HOF_SOURCE_ADMIN)
            self.assertEqual(by_player[int(incoming.id)].inducted_year, 1967)
            self.assertEqual(by_player[int(incoming.id)].member_kind, "goalie")
            self.assertEqual(by_player[int(incoming.id)].source, HOF_SOURCE_CSV)

        self._run_for_each_league(body)

    def test_csv_updates_existing_csv_row_in_place_on_all_leagues(self) -> None:
        def body(app, tmp: Path, slug: str) -> None:
            player = Player(
                first_name="Gump",
                last_name="Worsley",
                full_name="Gump Worsley",
                position="G",
                fhm_player_id="2972",
            )
            db.session.add(player)
            db.session.flush()
            db.session.add(
                HallOfFameMember(
                    player_id=int(player.id),
                    member_kind="goalie",
                    inducted_year=1968,
                    sort_order=1,
                    source=HOF_SOURCE_CSV,
                )
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            (raw / "hall_of_fame.csv").write_text(
                "fhm_player_id,kind,inducted_year,sort_order\n"
                "2972,goalie,1969,5\n",
                encoding="utf-8",
            )

            upserted = import_hall_of_fame(raw, app)
            self.assertEqual(upserted, 1, slug)
            self.assertEqual(
                db.session.scalar(select(func.count()).select_from(HallOfFameMember)) or 0,
                1,
            )
            row = db.session.scalar(select(HallOfFameMember).limit(1))
            assert row is not None
            self.assertEqual(row.inducted_year, 1969)
            self.assertEqual(row.sort_order, 5)
            self.assertEqual(row.source, HOF_SOURCE_CSV)

        self._run_for_each_league(body)

    def test_empty_csv_file_does_not_wipe_on_all_leagues(self) -> None:
        """Header-only / empty CSVs must not clear existing inductees."""

        def body(app, tmp: Path, slug: str) -> None:
            player = Player(
                first_name="Keep",
                last_name="Me",
                full_name="Keep Me",
                position="C",
                fhm_player_id="4242",
            )
            db.session.add(player)
            db.session.flush()
            db.session.add(
                HallOfFameMember(
                    player_id=int(player.id),
                    member_kind="skater",
                    inducted_year=1969,
                    sort_order=0,
                    source=HOF_SOURCE_CSV,
                )
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            (raw / "hall_of_fame.csv").write_text(
                "fhm_player_id,kind,inducted_year,sort_order\n",
                encoding="utf-8",
            )

            upserted = import_hall_of_fame(raw, app)
            self.assertEqual(upserted, 0, slug)
            row = db.session.scalar(select(HallOfFameMember).limit(1))
            assert row is not None
            self.assertEqual(row.inducted_year, 1969)
            self.assertEqual(row.source, HOF_SOURCE_CSV)

        self._run_for_each_league(body)


if __name__ == "__main__":
    unittest.main()
