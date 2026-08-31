"""Draft Hub order generation from standings and pick ownership."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.services.draft_hub_order import (
    _standing_worst_first_key,
    default_picks_per_round,
    generate_draft_order_from_prior_season,
    pick_ownership_lookup,
    resolve_prior_season_for_draft,
    standings_worst_to_best_for_draft_year,
)


class DraftHubOrderTest(unittest.TestCase):
    def test_standing_worst_first_sorts_by_points(self) -> None:
        low = MagicMock(pts=50, w=20, gf=150, ga=200, team=MagicMock(full_display_name=lambda: "A"))
        high = MagicMock(pts=90, w=45, gf=250, ga=180, team=MagicMock(full_display_name=lambda: "B"))
        self.assertLess(_standing_worst_first_key(low), _standing_worst_first_key(high))

    def test_pick_ownership_lookup_maps_round_and_original_fhm(self) -> None:
        row = MagicMock(
            original_team_fhm_id=5,
            round=2,
            owner_team_id=102,
        )
        site = MagicMock()
        site.scalars.return_value.all.return_value = [row]
        out = pick_ownership_lookup(site, league_slug="bowl-historical", draft_year=1969)
        self.assertEqual(out[(2, 5)], 102)

    def test_resolve_prior_season_skips_zero_record_current_year(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            season = resolve_prior_season_for_draft(db.session, draft_year=1971)
            if season is not None:
                from app.services.draft_hub_order import main_league_standings_worst_to_best
                from app.services.draft_hub_order import _standings_have_record_data

                rows = main_league_standings_worst_to_best(db.session, season)
                self.assertTrue(_standings_have_record_data(rows))

    def test_historical_1971_order_uses_prior_season_not_alphabetical(self) -> None:
        """1971 draft must not rank 0-0-0 current-year clubs A–Z (BOS first)."""
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            standings, label = standings_worst_to_best_for_draft_year(db.session, 1971)
            self.assertGreaterEqual(len(standings), 14, msg=label)
            abbrs = [row.team.abbreviation for row in standings if row.team is not None]
            self.assertEqual(abbrs[0], "BUF")
            self.assertEqual(abbrs[1], "VAN")
            self.assertEqual(abbrs[2], "PHI")
            self.assertEqual(abbrs[3], "STL")
            self.assertEqual(abbrs[4], "MIN")
            self.assertNotEqual(abbrs[0], "BOS")
            self.assertEqual(label, "1970-71")

    def test_all_hockey_leagues_use_last_season_not_zero_table(self) -> None:
        """Historical, Relegation, and Cap all seed from draft_year-1, never A–Z zeros."""
        cases = (
            ("bowl-historical", 1971, "1970-71", 14),
            ("bowl-fantasy", 1987, "1986-87", 24),
            ("bowl-cap", 2001, "2000-01", 30),
        )
        for slug, year, expected_label, min_teams in cases:
            with self.subTest(slug=slug):
                app = create_app(make_league_config(slug))
                with app.app_context():
                    standings, label = standings_worst_to_best_for_draft_year(db.session, year)
                    self.assertEqual(label, expected_label)
                    self.assertGreaterEqual(len(standings), min_teams, msg=slug)
                    abbrs = [row.team.abbreviation for row in standings if row.team is not None]
                    self.assertNotEqual(abbrs, sorted(abbrs), msg=f"{slug} sorted alphabetically")
                    self.assertEqual(default_picks_per_round(db.session), min_teams)

    def test_admin_generate_copy_names_all_three_sites(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_draft_hub_edit.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("Historical, Relegation, and Cap", text)
        self.assertIn("worst record → pick 1", text)

    def test_generate_applies_traded_owner(self) -> None:
        draft = MagicMock(
            id=1,
            status="setup",
            timeline_year=1969,
            rounds=1,
            picks_per_round=2,
        )
        t_worst = MagicMock(id=10, fhm_team_id="5", fhm_league_id=None, full_display_name=lambda: "Worst")
        t_best = MagicMock(id=11, fhm_team_id="8", fhm_league_id=None, full_display_name=lambda: "Best")
        st_worst = MagicMock(pts=40, w=10, gf=100, ga=200, team=t_worst)
        st_best = MagicMock(pts=90, w=50, gf=250, ga=150, team=t_best)
        season = MagicMock(id=3, start_year=1968, label="1968-69")

        league = MagicMock()
        league.scalars.return_value.all.return_value = [st_worst, st_best]

        site = MagicMock()
        site.scalars.return_value.all.return_value = [
            MagicMock(original_team_fhm_id=5, round=1, owner_team_id=99),
        ]

        with (
            unittest.mock.patch(
                "app.services.draft_hub_order.resolve_prior_season_for_draft",
                return_value=season,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_order.draft_pick_ownership_exists",
                return_value=True,
            ),
        ):
            created, err, summary = generate_draft_order_from_prior_season(
                league,
                site,
                league_slug="bowl-historical",
                draft=draft,
                preserve_penalty_picks={2},
            )
        self.assertIsNone(err)
        self.assertEqual(created, 2)
        self.assertEqual(summary.get("traded_count"), 1)
        added = [call.args[0] for call in site.add.call_args_list]
        self.assertEqual(len(added), 2)
        first_slot = added[0]
        self.assertEqual(first_slot.original_team_id, 10)
        self.assertEqual(first_slot.team_id, 99)
        self.assertFalse(first_slot.penalty_pick)
        self.assertEqual(added[1].original_team_id, 11)
        self.assertEqual(added[1].team_id, 11)
        self.assertTrue(added[1].penalty_pick)

    def test_generate_includes_cap_strike_summary_warnings(self) -> None:
        draft = MagicMock(
            id=1,
            status="setup",
            timeline_year=1969,
            rounds=1,
            picks_per_round=2,
        )
        t_worst = MagicMock(id=10, fhm_team_id="5", fhm_league_id=None, full_display_name=lambda: "Worst")
        t_best = MagicMock(id=11, fhm_team_id="8", fhm_league_id=None, full_display_name=lambda: "Best")
        st_worst = MagicMock(pts=40, w=10, gf=100, ga=200, team=t_worst)
        st_best = MagicMock(pts=90, w=50, gf=250, ga=150, team=t_best)
        season = MagicMock(id=3, start_year=1968, label="1968-69")
        league = MagicMock()
        league.scalars.return_value.all.return_value = [st_worst, st_best]
        site = MagicMock()
        site.scalars.return_value.all.return_value = []

        with (
            unittest.mock.patch(
                "app.services.draft_hub_order.resolve_prior_season_for_draft",
                return_value=season,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_order.draft_pick_ownership_exists",
                return_value=True,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_order.apply_cycle_strikes_to_slots",
                return_value=(1, ["Hamilton Strike 2 not applied"]),
            ),
        ):
            _created, _err, summary = generate_draft_order_from_prior_season(
                league,
                site,
                league_slug="bowl-cap",
                draft=draft,
            )
        self.assertEqual(summary.get("auto_penalties_applied"), 1)
        self.assertEqual(summary.get("strike_warnings"), ["Hamilton Strike 2 not applied"])

    def test_draft_hub_admin_template_has_penalty_checkbox(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_draft_hub_edit.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("Penalty pick", text)
        self.assertIn("slot_penalty_", text)

    def test_penalty_pick_column_is_bootstrapped(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "db_utils.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN penalty_pick BOOLEAN NOT NULL DEFAULT 0", text)

    def test_public_draft_hub_uses_draft_timeline_logos(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "draft_hub.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("_draft_logo_context_year", text)
        self.assertIn("team_logo_url_for_season_context", text)
        self.assertNotIn("from app.logo_urls import team_logo_url_for_team", text)

    def test_public_draft_order_displays_two_rounds(self) -> None:
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "draft_hub.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("Next two rounds only", text)
        self.assertIn("maxOrderRows = Math.max(1, Number(x.picks_per_round) || 27) * 2", text)
        self.assertIn("var n = Math.max(1, Number(picksPerRound) || 27) * 2", text)


if __name__ == "__main__":
    unittest.main()
