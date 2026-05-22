"""Tests for team alumni regular-season aggregation helpers."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.team_alumni import _dedupe_alumni_regular_season_lines


class TeamAlumniDedupeTests(unittest.TestCase):
    def test_dedupe_prefers_rs_over_retired_rs_per_player_season(self) -> None:
        duplicate_retired = SimpleNamespace(
            player_id=207,
            season_year=1964,
            team_fhm_id=5,
            league_fhm_id=0,
            career_source="retired_rs",
            gp=70,
        )
        duplicate_active = SimpleNamespace(
            player_id=207,
            season_year=1964,
            team_fhm_id=5,
            league_fhm_id=0,
            career_source="rs",
            gp=70,
        )
        same_season_other_player = SimpleNamespace(
            player_id=208,
            season_year=1964,
            team_fhm_id=5,
            league_fhm_id=0,
            career_source="rs",
            gp=70,
        )

        out = _dedupe_alumni_regular_season_lines(
            [duplicate_retired, same_season_other_player, duplicate_active]
        )

        self.assertEqual(len(out), 2)
        self.assertIn(duplicate_active, out)
        self.assertIn(same_season_other_player, out)
        self.assertNotIn(duplicate_retired, out)


if __name__ == "__main__":
    unittest.main()
