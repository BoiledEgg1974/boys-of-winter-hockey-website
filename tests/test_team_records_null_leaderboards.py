"""Team-record leaderboards ignore literal CSV NULL sentinels."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.team_records import _leaderboard_rows


class TeamRecordsNullLeaderboardTest(unittest.TestCase):
    def test_null_sentinel_rows_are_excluded_but_zero_is_valid(self) -> None:
        null_row = SimpleNamespace(
            gp=82,
            ppg_against=None,
            null_columns_csv="ppg_against",
            team=None,
            team_name_override="Null Team",
            season_year_label="2001-02",
            logo_file_override=None,
        )
        zero_row = SimpleNamespace(
            gp=82,
            ppg_against=0,
            null_columns_csv="",
            team=None,
            team_name_override="Zero Team",
            season_year_label="2002-03",
            logo_file_override=None,
        )

        rows = _leaderboard_rows(
            [null_row, zero_row],
            attr="ppg_against",
            maximize=False,
            use_min_gp=True,
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["team_name"], "Zero Team")
        self.assertEqual(rows[0]["value"], "0")


if __name__ == "__main__":
    unittest.main()
