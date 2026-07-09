"""Tests for auto-sync of team season records from career imports."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.team_season_record_sync import (
    _TeamAgg,
    _csv_covered_year_labels,
    _purge_import_rows_for_csv_seasons,
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


if __name__ == "__main__":
    unittest.main()
