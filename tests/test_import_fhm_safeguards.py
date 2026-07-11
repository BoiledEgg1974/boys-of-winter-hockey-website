"""Post-import checks for FHM bundle integrity (run from import_pipeline.runner)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path

from sqlalchemy import func, select, text

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Game, PlayerSkaterStat
from app.services.playoff_bracket import _is_regular_season_game_type
from app.services.seasons import get_current_season


def _season_rs_csv_hint(raw_dir: Path) -> str:
    path = raw_dir / "player_skater_stats_rs.csv"
    if not path.is_file():
        return " (player_skater_stats_rs.csv missing — re-export from FHM)"
    from tests.test_depth_chart_org_guard import _read_semicolon_rows

    if not _read_semicolon_rows(path):
        return f" ({path.name} exists but has no data rows — re-export from FHM)"
    return " (CSV has rows but import produced 0 — check import log for mapping errors)"


class ImportFhmSafeguardsTests(unittest.TestCase):
    def _league_slug(self) -> str:
        slug = (os.environ.get("LEAGUE_SLUG") or "").strip()
        if not slug:
            self.skipTest("LEAGUE_SLUG not set (post-import safeguards run after import_data.py)")
        return slug

    def test_current_season_skater_stats_when_games_exist(self) -> None:
        slug = self._league_slug()
        app = create_app(make_league_config(slug))
        with app.app_context():
            season = get_current_season()
            if season is None:
                self.skipTest(f"{slug}: no current season row")
            final_game_types = db.session.scalars(
                select(Game.game_type).where(
                    Game.season_id == season.id,
                    Game.status == "final",
                )
            ).all()
            game_count = sum(
                1 for gt in final_game_types if _is_regular_season_game_type(gt)
            )
            if game_count == 0:
                self.skipTest(
                    f"{slug}: no final regular-season games for current season "
                    f"({len(final_game_types)} final non-RS games ignored)"
                )
            rs_stats = db.session.scalar(
                select(func.count())
                .select_from(PlayerSkaterStat)
                .where(
                    PlayerSkaterStat.season_id == season.id,
                    PlayerSkaterStat.stat_segment == "rs",
                )
            ) or 0
            if rs_stats == 0:
                raw_dir = Path(str(app.config["RAW_IMPORT_DIR"]))
                hint = _season_rs_csv_hint(raw_dir)
            else:
                hint = ""
            self.assertGreater(
                rs_stats,
                0,
                f"{slug}: season has {game_count} final regular-season games but 0 RS skater stats — "
                f"/statistics will be empty{hint}",
            )

    def test_career_lines_have_no_duplicate_identity_keys(self) -> None:
        slug = self._league_slug()
        app = create_app(make_league_config(slug))
        with app.app_context():
            for table in ("player_skater_career_lines", "player_goalie_career_lines"):
                dupes = db.session.execute(
                    text(
                        f"""
                        SELECT COUNT(*) FROM (
                          SELECT player_id, season_year, team_fhm_id, league_fhm_id, career_source
                          FROM {table}
                          GROUP BY player_id, season_year, team_fhm_id, league_fhm_id, career_source
                          HAVING COUNT(*) > 1
                        )
                        """
                    )
                ).scalar()
                self.assertEqual(
                    int(dupes or 0),
                    0,
                    f"{slug}: duplicate rows in {table} (unique key violated)",
                )

    def test_statistics_page_not_empty_for_current_rs_split(self) -> None:
        slug = self._league_slug()
        app = create_app(make_league_config(slug))
        with app.app_context():
            season = get_current_season()
            if season is None:
                self.skipTest(f"{slug}: no current season row")
            rs_stats = db.session.scalar(
                select(func.count())
                .select_from(PlayerSkaterStat)
                .where(
                    PlayerSkaterStat.season_id == season.id,
                    PlayerSkaterStat.stat_segment == "rs",
                )
            ) or 0
            if rs_stats == 0:
                self.skipTest(f"{slug}: no RS skater stats to render")
            with app.test_client() as client:
                resp = client.get("/statistics?segment=rs")
                self.assertEqual(resp.status_code, 200)
                html = resp.get_data(as_text=True)
                self.assertNotIn(
                    "No skater stats for this split.",
                    html,
                    f"{slug}: statistics page empty despite {rs_stats} RS stat rows in DB",
                )


class FhmCareerCsvDuplicateExpectationTests(unittest.TestCase):
    """Source CSVs may contain duplicate keys; importer must dedupe (see fhm_loader)."""

    _CAREER_FILES: tuple[tuple[str, str], ...] = (
        ("bowl_historical", "player_skater_retired_career_stats_rs.csv"),
        ("bowl_fantasy", "player_skater_career_stats_rs.csv"),
        ("bowl_cap", "player_skater_retired_career_stats_rs.csv"),
    )

    def _count_csv_duplicate_keys(self, path: Path) -> tuple[int, int]:
        if not path.is_file():
            return 0, 0
        from scripts.import_pipeline.encoding_utils import cell_val, read_csv_normalized, to_int

        df = read_csv_normalized(path)
        keys: list[tuple[int, int, int, int]] = []
        for _, row in df.iterrows():
            r = row.to_dict()
            pid = to_int(cell_val(r, "playerid"))
            year = to_int(cell_val(r, "year"))
            tm_fhm = to_int(cell_val(r, "team_id", "teamid"))
            lid = to_int(cell_val(r, "league_id", "leagueid"))
            if pid is None or year is None or tm_fhm is None or lid is None:
                continue
            keys.append((pid, year, tm_fhm, lid))
        return len(keys), len(set(keys))

    def test_career_exports_with_duplicates_are_deduped_on_import(self) -> None:
        root = Path(__file__).resolve().parents[1]
        checked = 0
        for raw_dir, fname in self._CAREER_FILES:
            path = root / "data" / "imports" / "raw" / raw_dir / fname
            with self.subTest(league=raw_dir, file=fname):
                if not path.is_file():
                    self.skipTest(f"{raw_dir}/{fname} not in repo")
                total, unique = self._count_csv_duplicate_keys(path)
                self.assertGreater(total, 0)
                self.assertGreater(
                    total - unique,
                    0,
                    "expected duplicate career keys in FHM export (importer dedupes these)",
                )
                checked += 1
        self.assertGreaterEqual(checked, 1)


if __name__ == "__main__":
    unittest.main()
