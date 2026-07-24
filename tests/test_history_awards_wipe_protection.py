"""Admin history award winners must survive CSV re-import without duplicates (all leagues)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import func, select

from app import create_app
from app.config import Config, league_slugs
from app.league_db import db
from app.models import HistoryAward, Player, Season
from app.services.admin_history_records import HISTORY_SOURCE_ADMIN, HISTORY_SOURCE_CSV
from scripts.import_pipeline.runner import import_history_awards


class HistoryAwardsAdminCollisionTests(unittest.TestCase):
    def _run_for_each_league(self, body) -> None:
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

    def test_replace_all_keeps_admin_and_skips_csv_collision_on_all_leagues(self) -> None:
        def body(app, tmp: Path, slug: str) -> None:
            season = Season(label="1998-99", start_year=1998, end_year=1999, is_current=False)
            csv_player = Player(
                first_name="Csv",
                last_name="Hart",
                full_name="Csv Hart",
                position="C",
                fhm_player_id="1",
            )
            admin_player = Player(
                first_name="Admin",
                last_name="Vezina",
                full_name="Admin Vezina",
                position="G",
                fhm_player_id="2",
            )
            incoming = Player(
                first_name="Csv",
                last_name="Vezina",
                full_name="Csv Vezina",
                position="G",
                fhm_player_id="3",
            )
            db.session.add_all([season, csv_player, admin_player, incoming])
            db.session.flush()
            db.session.add_all(
                [
                    HistoryAward(
                        season_id=int(season.id),
                        award_name="HART TROPHY",
                        player_id=int(csv_player.id),
                        notes="sheet_season=1998-99",
                        source=HISTORY_SOURCE_CSV,
                    ),
                    HistoryAward(
                        season_id=int(season.id),
                        award_name="VEZINA TROPHY",
                        player_id=int(admin_player.id),
                        notes="sheet_season=1998-99",
                        source=HISTORY_SOURCE_ADMIN,
                    ),
                ]
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            (raw / "history_awards.csv").write_text(
                "season,award_name,player_id,team_id,notes\n"
                "1998-99,HART TROPHY,1,,\n"
                "1998-99,VEZINA TROPHY,3,,\n",
                encoding="utf-8",
            )

            imported = import_history_awards(raw, app, replace_all=True)
            self.assertEqual(imported, 1, slug)

            rows = list(db.session.scalars(select(HistoryAward)).all())
            self.assertEqual(len(rows), 2, slug)

            by_name = {(" ".join(r.award_name.upper().split())): r for r in rows}
            admin_row = by_name["VEZINA TROPHY"]
            self.assertEqual(admin_row.source, HISTORY_SOURCE_ADMIN)
            self.assertEqual(admin_row.player_id, int(admin_player.id))

            hart = by_name["HART TROPHY"]
            self.assertEqual(hart.source, HISTORY_SOURCE_CSV)
            self.assertEqual(hart.player_id, int(csv_player.id))

            vezina_count = sum(
                1 for r in rows if " ".join(r.award_name.upper().split()) == "VEZINA TROPHY"
            )
            self.assertEqual(vezina_count, 1, slug)

        self._run_for_each_league(body)

    def test_whitespace_award_name_variants_still_collide_on_all_leagues(self) -> None:
        def body(app, tmp: Path, slug: str) -> None:
            season = Season(label="1998-99", start_year=1998, end_year=1999, is_current=False)
            admin_player = Player(
                first_name="Admin",
                last_name="Jennings",
                full_name="Admin Jennings",
                position="G",
                fhm_player_id="9",
            )
            csv_player = Player(
                first_name="Csv",
                last_name="Jennings",
                full_name="Csv Jennings",
                position="G",
                fhm_player_id="10",
            )
            db.session.add_all([season, admin_player, csv_player])
            db.session.flush()
            db.session.add(
                HistoryAward(
                    season_id=int(season.id),
                    award_name="WILLIAM JENNINGS  TROPHY",
                    player_id=int(admin_player.id),
                    notes="sheet_season=1998-99",
                    source=HISTORY_SOURCE_ADMIN,
                )
            )
            db.session.commit()

            raw = tmp / "raw"
            raw.mkdir()
            (raw / "history_awards.csv").write_text(
                "season,award_name,player_id,team_id,notes\n"
                "1998-99,WILLIAM JENNINGS TROPHY,10,,\n",
                encoding="utf-8",
            )

            imported = import_history_awards(raw, app, replace_all=True)
            self.assertEqual(imported, 0, slug)
            self.assertEqual(
                db.session.scalar(select(func.count()).select_from(HistoryAward)) or 0,
                1,
            )
            row = db.session.scalar(select(HistoryAward).limit(1))
            assert row is not None
            self.assertEqual(row.source, HISTORY_SOURCE_ADMIN)
            self.assertEqual(row.player_id, int(admin_player.id))

        self._run_for_each_league(body)


if __name__ == "__main__":
    unittest.main()
