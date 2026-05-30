"""League History award display helpers."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import HistoryAward
from app.routes.main import _build_award_panels, _history_award_start_year
from app.services.history_coach_awards import resolve_staff_history_display


class HistoryAwardsTests(unittest.TestCase):
    def test_award_logo_year_prefers_sheet_season_notes(self) -> None:
        award = HistoryAward(season_id=1, award_name="BOURQUE TROPHY", notes="sheet_season=1979-80")

        self.assertEqual(_history_award_start_year(award), 1979)

    def test_jim_gregory_staff_row_uses_csv_gm_label(self) -> None:
        award = HistoryAward(
            season_id=1,
            award_name="JIM GREGORY TROPHY",
            staff_fhm_id="2080",
            notes="unresolved_team=Parchie",
        )

        with patch(
            "app.services.history_coach_awards._staff_row_by_fhm_id",
            return_value={
                "staffid": "2080",
                "teamid": None,
                "full_name": "Wrong Staff Name",
                "retired": False,
            },
        ):
            display = resolve_staff_history_display(
                session=None,  # type: ignore[arg-type]
                award=award,
                coach_candidates=None,
                raw_dir=Path("."),
            )

        self.assertIsNotNone(display)
        assert display is not None
        self.assertEqual(display.full_name, "Parchie")

    def test_award_panels_group_whitespace_variants(self) -> None:
        old = HistoryAward(
            id=1,
            season_id=1,
            award_name="WILLIAM JENNINGS  TROPHY",
            player_id=1,
            notes="sheet_season=1967-68",
        )
        latest = HistoryAward(
            id=2,
            season_id=1,
            award_name="WILLIAM JENNINGS TROPHY",
            player_id=2,
            notes="sheet_season=1968-69",
        )

        with (
            patch("app.routes.main._history_award_trophy_stem_map", return_value={}),
            patch("app.routes.main._history_award_trophy_rel_from_map", return_value=None),
        ):
            panels = _build_award_panels([old, latest])

        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0]["award_name"], "WILLIAM JENNINGS  TROPHY")
        self.assertEqual(panels[0]["featured"], latest)
        self.assertEqual(panels[0]["past"], [old])


if __name__ == "__main__":
    unittest.main()
