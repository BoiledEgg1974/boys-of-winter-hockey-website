"""Homepage dashboard schedule anchor regressions."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock

from app.services.homepage_dashboard import league_calendar_anchor_date


class HomepageDashboardScheduleTests(unittest.TestCase):
    def test_anchor_uses_latest_final_game_date_when_available(self) -> None:
        session = MagicMock()
        session.scalar.side_effect = [date(1970, 11, 15)]

        anchor = league_calendar_anchor_date(session, 1)

        self.assertEqual(anchor, date(1970, 11, 15))
        self.assertEqual(session.scalar.call_count, 1)

    def test_anchor_uses_earliest_scheduled_game_when_no_finals_exist(self) -> None:
        session = MagicMock()
        session.scalar.side_effect = [None, date(1970, 9, 20)]

        anchor = league_calendar_anchor_date(session, 1)

        self.assertEqual(anchor, date(1970, 9, 20))
        self.assertEqual(session.scalar.call_count, 2)


if __name__ == "__main__":
    unittest.main()
