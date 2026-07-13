"""Org development month diffs, timeline, archive, and team prospects helpers."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from app.services.org_development import classify_player_month_diff, format_signed_delta
from app.services.org_development_timeline import (
    ORG_DEV_ARCHIVE_MONTH_LIMIT,
    development_report_title,
    hockey_season_start_year,
    timeline_from_date,
    timeline_sort_key,
)
from app.services.team_prospects import (
    develop_rate_from_snapshots,
    format_develop_rate,
    format_draft_details,
    ordinal_suffix,
)


class OrgDevelopmentClassifyTest(unittest.TestCase):
    def test_progression_when_attrs_rise(self) -> None:
        first = {"passing": 10.0, "balance": 8.0, "strength": 12.0}
        last = {"passing": 11.0, "balance": 9.0, "strength": 12.0}
        card = classify_player_month_diff(
            first,
            last,
            is_goalie=False,
            overall=40,
            ability=0.5,
            potential=2.0,
        )
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card["side"], "progression")
        self.assertGreaterEqual(card["improved_count"], 2)

    def test_format_signed_delta(self) -> None:
        self.assertEqual(format_signed_delta(1.0), "+1")
        self.assertEqual(format_signed_delta(-1.0), "-1")


class OrgDevelopmentTimelineTest(unittest.TestCase):
    def test_hockey_season_start_year(self) -> None:
        self.assertEqual(hockey_season_start_year(date(1968, 10, 1)), 1968)
        self.assertEqual(hockey_season_start_year(date(1969, 3, 15)), 1968)

    def test_timeline_label_includes_season(self) -> None:
        tl = timeline_from_date(date(1968, 10, 1))
        title = development_report_title(
            calendar_year=int(tl["timeline_calendar_year"]),
            calendar_month=int(tl["timeline_calendar_month"]),
            season_start_year=int(tl["timeline_season_start_year"]),
        )
        self.assertIn("1968-69", title)
        self.assertIn("October", title)

    def test_timeline_sort_key_orders_hockey_months(self) -> None:
        july = timeline_sort_key(1968, 1968, 7)
        october = timeline_sort_key(1968, 1968, 10)
        january = timeline_sort_key(1968, 1969, 1)
        self.assertLess(july, october)
        self.assertLess(october, january)

    def test_archive_limit_constant(self) -> None:
        self.assertEqual(ORG_DEV_ARCHIVE_MONTH_LIMIT, 36)


class TeamProspectsHelpersTest(unittest.TestCase):
    def test_format_draft_details_drafted(self) -> None:
        d = format_draft_details(team_name="Calgary Flames", draft_year=2041, overall_pick=17)
        self.assertEqual(d["pick_line"], "2041 · 17th Overall")

    def test_develop_rate_percent(self) -> None:
        snaps = [SimpleNamespace(overall_score=48), SimpleNamespace(overall_score=60)]
        delta, pct = develop_rate_from_snapshots(snaps)
        self.assertEqual(delta, 12)
        self.assertAlmostEqual(float(pct or 0), 25.0)

    def test_develop_rate_zero_start_falls_back_to_points(self) -> None:
        snaps = [SimpleNamespace(overall_score=0), SimpleNamespace(overall_score=5)]
        delta, pct = develop_rate_from_snapshots(snaps)
        self.assertEqual(delta, 5)
        self.assertIsNone(pct)

    def test_format_develop_rate_percent_display(self) -> None:
        fmt = format_develop_rate(12, 25.0)
        self.assertEqual(fmt["display"], "+25%")
        self.assertEqual(fmt["kind"], "up")

    def test_format_develop_rate_negative_percent(self) -> None:
        fmt = format_develop_rate(-5, -8.3)
        self.assertEqual(fmt["display"], "-8.3%")
        self.assertEqual(fmt["kind"], "down")

    def test_ordinal_suffix(self) -> None:
        self.assertEqual(ordinal_suffix(17), "17th")


if __name__ == "__main__":
    unittest.main()
