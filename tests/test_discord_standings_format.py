from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.discord_interactions import _discord_standings_record
from app.services.homepage_dashboard import standing_row_json


class DiscordStandingsFormatTests(unittest.TestCase):
    def test_discord_record_uses_gp_w_l_t_without_otl_label(self) -> None:
        st = SimpleNamespace(
            gp=74,
            w=34,
            l=35,
            ties=4,
            otl=1,
            standing_gp_display=lambda: 73,
        )

        out = _discord_standings_record(st)  # type: ignore[arg-type]

        self.assertEqual(out, "74 GP, 34 W, 35 L, 4 T")
        self.assertNotIn("OT", out)
        self.assertNotIn("OTL", out)

    def test_dashboard_standings_row_prefers_imported_gp(self) -> None:
        st = SimpleNamespace(
            gp=77,
            w=28,
            l=42,
            ties=7,
            pts=63,
            conference="",
            division="Central",
            standing_gp_display=lambda: 76,
        )
        tm = SimpleNamespace(
            slug="st-louis-blues",
            abbreviation="STL",
            full_display_name=lambda: "St. Louis Blues",
        )

        with patch("app.services.homepage_dashboard.dashboard_team_logo_url", return_value=""):
            out = standing_row_json(st, tm, 2)  # type: ignore[arg-type]

        self.assertEqual(out["gp"], 77)
        self.assertEqual(out["w"], 28)
        self.assertEqual(out["l"], 42)
        self.assertEqual(out["ties"], 7)
        self.assertNotIn("otl", out)


if __name__ == "__main__":
    unittest.main()
