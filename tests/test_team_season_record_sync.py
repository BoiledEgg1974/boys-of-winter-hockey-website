"""Tests for auto-sync of team season records from career imports."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.team_season_record_sync import (
    MIN_GP_IMPORT_STANDINGS_SEASON,
    _TeamAgg,
    _csv_covered_year_labels,
    _import_season_aggs_complete,
    _load_archived_team_stats,
    _parse_archived_team_stats_row,
    _purge_import_rows_for_csv_seasons,
    _supplement_special_teams_from_archive,
    _team_fhm_str,
    _year_label,
)


class TeamSeasonRecordSyncTests(unittest.TestCase):
    def test_year_label(self) -> None:
        self.assertEqual(_year_label(1999), "1999-00")
        self.assertEqual(_year_label(1998), "1998-99")

    def test_playoff_result_mapping(self) -> None:
        champ = _TeamAgg(team_fhm_id="15", max_po_gp=26)
        runner = _TeamAgg(team_fhm_id="23", max_po_gp=21)
        miss = _TeamAgg(team_fhm_id="5", max_po_gp=0)
        self.assertEqual(champ.playoff_result(), "BOWL CUP CHAMPION")
        self.assertEqual(runner.playoff_result(), "Lost Cup Finals")
        self.assertEqual(miss.playoff_result(), "Missed Playoffs")

    def test_pts_and_gp(self) -> None:
        agg = _TeamAgg(team_fhm_id="1", w=50, l=25, otl=7, gf=250, ga=200)
        self.assertEqual(agg.gp, 82)
        self.assertEqual(agg.pts, 107)
        self.assertEqual(agg.goal_diff, 50)

    def test_team_fhm_str_preserves_zero(self) -> None:
        self.assertEqual(_team_fhm_str(0), "0")
        self.assertEqual(_team_fhm_str("0"), "0")
        self.assertIsNone(_team_fhm_str(None))
        self.assertIsNone(_team_fhm_str(""))

    def test_csv_covered_labels_and_purge(self) -> None:
        class _FakeSession:
            def scalars(self, _q):
                return self

            def all(self):
                return [
                    SimpleNamespace(
                        season_year_label="1930-31",
                        source="csv",
                    ),
                    SimpleNamespace(
                        season_year_label="1930-31",
                        source="import",
                    ),
                    SimpleNamespace(
                        season_year_label="1999-00",
                        source="import",
                    ),
                ]

            def delete(self, rec) -> None:
                self.deleted.append(rec)

            deleted: list[object]

        session = _FakeSession()
        session.deleted = []
        self.assertEqual(_csv_covered_year_labels(session), {"1930-31"})
        removed = _purge_import_rows_for_csv_seasons(session)
        self.assertEqual(removed, 1)
        self.assertEqual(len(session.deleted), 1)
        self.assertEqual(session.deleted[0].season_year_label, "1930-31")

    def test_import_season_complete_threshold(self) -> None:
        partial = {"1": _TeamAgg(team_fhm_id="1", w=1, l=1, otl=1)}
        full = {"1": _TeamAgg(team_fhm_id="1", w=40, l=30, otl=12)}
        self.assertFalse(_import_season_aggs_complete(partial))
        self.assertTrue(_import_season_aggs_complete(full))
        self.assertEqual(MIN_GP_IMPORT_STANDINGS_SEASON, 20)

    def test_parse_archived_team_stats_row(self) -> None:
        row = {
            "ppg": "66",
            "pp_ch": "372",
            "pp_ga": "22",
            "sh_ch": "355",
            "sh_ga": "7",
        }
        parsed = _parse_archived_team_stats_row(row)
        self.assertEqual(parsed["pp_chances"], 372)
        self.assertEqual(parsed["ppg_against"], 22)
        self.assertEqual(parsed["sh_chances"], 355)
        self.assertEqual(parsed["pp_pct"], 17.7)
        self.assertEqual(parsed["pk_pct"], 93.8)

    def test_load_archived_team_stats_bowl_cap(self) -> None:
        from pathlib import Path

        raw = Path("data/imports/raw/bowl_cap")
        archived = _load_archived_team_stats(raw)
        self.assertIn("1999-00", archived)
        self.assertIn("2000-01", archived)
        self.assertEqual(archived["1999-00"]["0"]["pp_chances"], 372)

    def test_supplement_special_teams_from_archive(self) -> None:
        from pathlib import Path
        from types import SimpleNamespace

        class _FakeSession:
            def scalars(self, _q):
                return self

            def all(self):
                return [
                    SimpleNamespace(
                        season_year_label="2000-01",
                        team_fhm_id_csv="0",
                        team_id=1,
                        pp_chances=None,
                        ppg_against=None,
                        sh_chances=None,
                        shg_against=None,
                        pp_pct=None,
                        pk_pct=None,
                        null_columns_csv=None,
                    )
                ]

            def get(self, _model, _id):
                return SimpleNamespace(fhm_team_id="0")

        raw = Path("data/imports/raw/bowl_cap")
        written = _supplement_special_teams_from_archive(_FakeSession(), raw_dir=raw)
        self.assertGreaterEqual(written, 4)


class TeamRecordsStandingsDisplayTests(unittest.TestCase):
    def test_records_have_displayable_standings(self) -> None:
        from app.models import TeamSeasonRecord
        from app.services.team_records import _records_have_displayable_standings

        csv_row = TeamSeasonRecord(season_year_label="1999-00", gp=82, source="csv")
        partial = TeamSeasonRecord(season_year_label="2000-01", gp=3, source="import")
        full_import = TeamSeasonRecord(season_year_label="2000-01", gp=82, source="import")
        self.assertTrue(_records_have_displayable_standings([csv_row]))
        self.assertFalse(_records_have_displayable_standings([partial]))
        self.assertTrue(_records_have_displayable_standings([full_import]))


if __name__ == "__main__":
    unittest.main()
