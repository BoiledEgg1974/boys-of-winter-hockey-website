"""Team-record leaderboards ignore literal CSV NULL sentinels."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.team_records import (
    _dedupe_team_season_records,
    _is_hidden_season_summary,
    _leaderboard_rows,
    _runner_up_override_team_fhm_id,
    team_display_name,
)


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

    def test_historical_1668_summary_is_hidden_only_for_historical_site(self) -> None:
        self.assertTrue(_is_hidden_season_summary("1668-69", "bowl-historical"))
        self.assertFalse(_is_hidden_season_summary("1668-69", "bowl-cap"))
        self.assertFalse(_is_hidden_season_summary("1917-18", "bowl-historical"))

    def test_historical_runner_up_overrides_for_known_missing_finals(self) -> None:
        self.assertEqual(_runner_up_override_team_fhm_id("1936-37", "bowl-historical"), "10")
        self.assertEqual(_runner_up_override_team_fhm_id("1939-40", "bowl-historical"), "3")
        self.assertIsNone(_runner_up_override_team_fhm_id("1939-40", "bowl-cap"))

    def test_team_display_name_prefers_override_over_linked_team(self) -> None:
        team = SimpleNamespace(full_display_name=lambda: "Philadelphia Flyers")
        rec = SimpleNamespace(team=team, team_name_override="Philadelphia Quakers")
        self.assertEqual(team_display_name(rec), "Philadelphia Quakers")

    def test_dedupe_prefers_csv_row_over_import_duplicate(self) -> None:
        csv_row = SimpleNamespace(
            season_year_label="1930-31",
            pts=12,
            w=4,
            l=36,
            gf=76,
            ga=184,
            source="csv",
            team_name_override="Philadelphia Quakers",
            team_id=10,
            team_fhm_id_csv="121",
        )
        import_row = SimpleNamespace(
            season_year_label="1930-31",
            pts=12,
            w=4,
            l=36,
            gf=76,
            ga=184,
            source="import",
            team_name_override=None,
            team_id=None,
            team_fhm_id_csv="7",
        )
        out = _dedupe_team_season_records([import_row, csv_row])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].source, "csv")
        self.assertEqual(out[0].team_name_override, "Philadelphia Quakers")


if __name__ == "__main__":
    unittest.main()
