"""Career-line import deduplication for duplicate FHM CSV rows."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app import create_app
from app.config import Config
from app.league_db import db
from app.models import Player, PlayerSkaterCareerLine
from scripts.import_pipeline.fhm_loader import import_career_skater_file


class FhmCareerImportDedupeTests(unittest.TestCase):
    def test_import_career_skater_file_keeps_last_duplicate_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db_path = tmp_path / "test.db"
            raw = tmp_path / "raw"
            raw.mkdir()
            csv_path = raw / "player_skater_retired_career_stats_rs.csv"
            csv_path.write_text(
                "PlayerId;Year;TeamId;LeagueId;GP;G;A;PIM\n"
                "99901;1922;18;2;30;2;3;2\n"
                "99901;1922;18;2;30;5;7;4\n",
                encoding="utf-8",
            )

            class _TestConfig(Config):
                SQLALCHEMY_DATABASE_URI = f"sqlite:///{db_path.as_posix()}"
                TESTING = True

            app = create_app(_TestConfig)
            with app.app_context():
                try:
                    db.create_all()
                    player = Player(
                        fhm_player_id="99901",
                        first_name="Test",
                        last_name="Skater",
                        full_name="Test Skater",
                    )
                    db.session.add(player)
                    db.session.commit()
                    players_fhm = {99901: player.id}
                    with patch(
                        "scripts.import_pipeline.fhm_loader.commit_with_sqlite_retry",
                        side_effect=lambda session: session.commit(),
                    ):
                        n = import_career_skater_file(
                            raw,
                            csv_path.name,
                            "retired_rs",
                            players_fhm,
                            {},
                        )
                    self.assertEqual(n, 1)
                    row = db.session.scalars(
                        select(PlayerSkaterCareerLine).where(
                            PlayerSkaterCareerLine.player_id == player.id,
                            PlayerSkaterCareerLine.career_source == "retired_rs",
                        )
                    ).one()
                    self.assertEqual(row.goals, 5)
                    self.assertEqual(row.assists, 7)
                    self.assertEqual(row.pim, 4)
                finally:
                    db.session.remove()
                    for engine in db.engines.values():
                        engine.dispose()


if __name__ == "__main__":
    unittest.main()
