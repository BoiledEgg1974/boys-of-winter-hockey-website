"""Draft Hub tracker summary and Perri pick value helpers."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.services.draft_pick_values import (
    FIRST_ROUND_BUCKET_VALUE,
    ROUND_AVERAGE_VALUE,
    perri_pick_value_exact,
    perri_pick_value_for_asset,
    pick_value_attribution,
)
from app.services.draft_hub_tracker import build_draft_hub_tracker


class DraftPickValuesTest(unittest.TestCase):
    def test_exact_anchor_values(self) -> None:
        self.assertEqual(perri_pick_value_exact(1), 100.0)
        self.assertEqual(perri_pick_value_exact(2), 72.69)
        self.assertEqual(perri_pick_value_exact(14), 26.46)

    def test_round_average_exists_for_seven_rounds(self) -> None:
        for rnd in range(1, 8):
            self.assertGreater(ROUND_AVERAGE_VALUE[rnd], 0.0)

    def test_first_round_bucket_fallback(self) -> None:
        val = perri_pick_value_for_asset(round_no=1, original_round1_position=3)
        self.assertEqual(val, FIRST_ROUND_BUCKET_VALUE["picks_1_5"])

    def test_attribution_links_to_puckpedia(self) -> None:
        attr = pick_value_attribution()
        self.assertIn("PuckPedia", attr.text)
        self.assertIn("puckpedia.com", attr.calculator_url)


class DraftHubTrackerTest(unittest.TestCase):
    def test_tracker_uses_slot_inventory(self) -> None:
        site = MagicMock()
        league = MagicMock()
        draft = MagicMock(id=9, timeline_year=2026, status="setup", scheduled_start_at=None)
        t1 = MagicMock(
            id=1,
            slug="team-a",
            abbreviation="TMA",
            full_display_name=lambda: "Team A",
            primary_color="#112233",
        )
        t2 = MagicMock(
            id=2,
            slug="team-b",
            abbreviation="TMB",
            full_display_name=lambda: "Team B",
            primary_color="#445566",
        )
        slot1 = MagicMock(
            forfeited=False,
            team_id=1,
            original_team_id=1,
            round=1,
            overall_pick=1,
        )
        slot2 = MagicMock(
            forfeited=False,
            team_id=2,
            original_team_id=2,
            round=2,
            overall_pick=35,
        )
        panel = MagicMock(draft_year=2026, round_count=7, status="active", display_order=1)
        site.scalars.return_value.all.side_effect = [
            [slot1, slot2],  # draft slots
            [],  # league drafts list
        ]
        league.scalar.return_value = None

        with unittest.mock.patch(
            "app.services.draft_hub_tracker._active_ownership_panel",
            return_value=panel,
        ):
            payload = build_draft_hub_tracker(
            site,
            league,
            league_slug="bowl-cap",
            featured_draft=draft,
            team_by_id={1: t1, 2: t2},
            team_logo_url=lambda _tm, _d: "/logo.png",
            team_page_url=lambda tm: f"/team/{tm.slug}",
            draft_hub_url=lambda: "/draft-hub",
            draft_archive_url=lambda: "/draft-hub/archive",
            draft_archive_one_url=lambda _id: "/draft-hub/archive/1",
            )

        self.assertTrue(payload["has_pick_data"])
        self.assertIsNotNone(payload["first_pick"])
        assert payload["first_pick"] is not None
        self.assertEqual(payload["first_pick"]["team_id"], 1)
        team_a = next(r for r in payload["team_breakdown"] if r["team_id"] == 1)
        self.assertEqual(team_a["pick_count"], 1)
        self.assertGreater(payload["highest_pick_value"]["value"], 0)

    def test_completed_featured_draft_defers_to_next_active_panel(self) -> None:
        site = MagicMock()
        league = MagicMock()
        completed = MagicMock(id=5, timeline_year=1969, status="completed", scheduled_start_at=None)
        panel = MagicMock(draft_year=1970, round_count=7, status="active", display_order=1)
        t1 = MagicMock(
            id=1,
            slug="la-kings",
            abbreviation="LAK",
            full_display_name=lambda: "Los Angeles Kings",
            primary_color="#552583",
        )
        t2 = MagicMock(
            id=2,
            slug="philadelphia-flyers",
            abbreviation="PHI",
            full_display_name=lambda: "Philadelphia Flyers",
            primary_color="#f74902",
        )
        ownership_row = MagicMock(
            owner_team_id=2,
            original_team_id=1,
            round=1,
        )
        site.scalars.return_value.all.side_effect = [
            [ownership_row],
            [],
        ]
        league.scalar.return_value = None

        with (
            unittest.mock.patch(
                "app.services.draft_hub_tracker._active_ownership_panel",
                return_value=panel,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._round1_position_by_team_id",
                return_value={1: 1, 2: 2},
            ),
        ):
            payload = build_draft_hub_tracker(
                site,
                league,
                league_slug="bowl-historical",
                featured_draft=completed,
                team_by_id={1: t1, 2: t2},
                team_logo_url=lambda _tm, _d: "/logo.png",
                team_page_url=lambda tm: f"/team/{tm.slug}",
                draft_hub_url=lambda: "/draft-hub",
                draft_archive_url=lambda: "/draft-hub/archive",
                draft_archive_one_url=lambda _id: "/draft-hub/archive/1",
            )

        self.assertEqual(payload["draft_year"], 1970)
        self.assertEqual(payload["status_label"], "Upcoming")
        self.assertEqual(payload["first_pick"]["team_id"], 2)
        self.assertEqual([x["label"] for x in payload["hub_links"]], ["Draft Archive"])

    def test_tracker_honors_admin_ownership_round_count_above_seven(self) -> None:
        site = MagicMock()
        league = MagicMock()
        panel = MagicMock(draft_year=2000, round_count=9, status="active", display_order=1)
        t1 = MagicMock(
            id=1,
            slug="atlanta-thrashers",
            abbreviation="ATL",
            full_display_name=lambda: "Atlanta Thrashers",
            primary_color="#4b2e83",
        )
        round_one = MagicMock(
            owner_team_id=1,
            original_team_id=1,
            round=1,
        )
        round_nine = MagicMock(
            owner_team_id=1,
            original_team_id=1,
            round=9,
        )
        site.scalars.return_value.all.side_effect = [
            [round_one, round_nine],
            [],
        ]
        league.scalar.return_value = None

        with (
            unittest.mock.patch(
                "app.services.draft_hub_tracker._active_ownership_panel",
                return_value=panel,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._round1_position_by_team_id",
                return_value={1: 1},
            ),
        ):
            payload = build_draft_hub_tracker(
                site,
                league,
                league_slug="bowl-cap",
                featured_draft=None,
                team_by_id={1: t1},
                team_logo_url=lambda _tm, _d: "/logo.png",
                team_page_url=lambda tm: f"/team/{tm.slug}",
                draft_hub_url=lambda: "/draft-hub",
                draft_archive_url=lambda: "/draft-hub/archive",
                draft_archive_one_url=lambda _id: "/draft-hub/archive/1",
            )

        self.assertEqual(payload["round_count"], 9)
        team = payload["team_breakdown"][0]
        self.assertEqual(team["pick_count"], 2)
        self.assertEqual(team["picks_by_round"]["9"], 1)

    def test_completed_featured_draft_never_drives_tracker_year(self) -> None:
        site = MagicMock()
        league = MagicMock()
        completed = MagicMock(id=5, timeline_year=1969, status="completed", scheduled_start_at=None)
        site.scalars.return_value.all.side_effect = [
            [],  # league drafts list
        ]

        with (
            unittest.mock.patch(
                "app.services.draft_hub_tracker.in_game_draft_ownership_cutoff_year",
                return_value=1970,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._active_ownership_panel",
                return_value=None,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._round1_position_by_team_id",
                return_value={},
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._latest_game_date",
                return_value=None,
            ),
        ):
            payload = build_draft_hub_tracker(
                site,
                league,
                league_slug="bowl-historical",
                featured_draft=completed,
                team_by_id={},
                team_logo_url=lambda _tm, _d: "/logo.png",
                team_page_url=lambda tm: f"/team/{tm.slug}",
                draft_hub_url=lambda: "/draft-hub",
                draft_archive_url=lambda: "/draft-hub/archive",
                draft_archive_one_url=lambda _id: "/draft-hub/archive/1",
            )

        self.assertEqual(payload["draft_year"], 1970)
        self.assertNotEqual(payload["status_label"], "Completed")
        self.assertEqual(payload["countdown_label"], "Draft date TBD")

    def test_stale_setup_draft_defers_to_current_ingame_year(self) -> None:
        site = MagicMock()
        league = MagicMock()
        stale_setup = MagicMock(id=7, timeline_year=1999, status="setup", scheduled_start_at=None)
        panel = MagicMock(draft_year=2000, round_count=7, status="active", display_order=1)
        t1 = MagicMock(
            id=1,
            slug="atlanta-thrashers",
            abbreviation="ATL",
            full_display_name=lambda: "Atlanta Thrashers",
            primary_color="#4b2e83",
        )
        ownership_row = MagicMock(
            owner_team_id=1,
            original_team_id=1,
            round=1,
        )
        site.scalars.return_value.all.side_effect = [
            [ownership_row],
            [],
        ]

        with (
            unittest.mock.patch(
                "app.services.draft_hub_tracker.in_game_draft_ownership_cutoff_year",
                return_value=2000,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._active_ownership_panel",
                return_value=panel,
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._round1_position_by_team_id",
                return_value={1: 1},
            ),
            unittest.mock.patch(
                "app.services.draft_hub_tracker._latest_game_date",
                return_value=None,
            ),
        ):
            payload = build_draft_hub_tracker(
                site,
                league,
                league_slug="bowl-cap",
                featured_draft=stale_setup,
                team_by_id={1: t1},
                team_logo_url=lambda _tm, _d: "/logo.png",
                team_page_url=lambda tm: f"/team/{tm.slug}",
                draft_hub_url=lambda: "/draft-hub",
                draft_archive_url=lambda: "/draft-hub/archive",
                draft_archive_one_url=lambda _id: "/draft-hub/archive/1",
            )

        self.assertEqual(payload["draft_year"], 2000)
        self.assertEqual(payload["status_label"], "Upcoming")
        self.assertEqual(payload["first_pick"]["team_id"], 1)

    def test_draft_hub_route_exposes_tracker_payload(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "draft_hub.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("build_draft_hub_tracker", text)
        self.assertIn('"tracker": tracker', text)
        self.assertIn("_public_draft_room(featured, min_draft_year=min_draft_year)", text)
        self.assertIn("min_draft_year=min_draft_year", text)
        self.assertIn("in_game_draft_ownership_cutoff_year", text)
        self.assertIn("featured = featured_draft(db.session, slug)", text)
        self.assertIn("tracker = _tracker_payload(featured, team_by_id)", text)
        self.assertIn('{"setup", "live"}', text)

    def test_draft_hub_template_has_tracker_sections(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "draft_hub.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("dh-tracker-shell", text)
        self.assertIn("dh-breakdown-shell", text)
        self.assertIn("dh-links-shell", text)
        self.assertIn("renderTrackerPanels", text)
        self.assertIn("dh-breakdown-mode", text)
        self.assertIn("dh-breakdown-axis", text)
        self.assertIn("--bar-count", text)
        self.assertIn("No live draft room is open", text)
        self.assertIn("archive", text)
        self.assertIn("Perri Pick Value Calculator", text)

    def test_draft_hub_css_has_tracker_styles(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "site.css"
        text = path.read_text(encoding="utf-8")
        self.assertIn(".dh-tracker__panel", text)
        self.assertIn(".dh-breakdown__chart", text)
        self.assertIn("@keyframes dh-breakdown-bar-rise", text)
        self.assertIn(".dh-breakdown-axis__line", text)
        self.assertIn(".dh-links__list", text)


if __name__ == "__main__":
    unittest.main()
