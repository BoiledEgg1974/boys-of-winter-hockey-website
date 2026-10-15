"""Expansion draft picks are commissioner-only (matches entry Draft Hub default)."""

from __future__ import annotations

import re
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import app.services.expansion_draft_state as expansion_draft_state
from app import create_app
from app.config import make_league_config


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else ""


class ExpansionDraftAdminOnlyTest(unittest.TestCase):
    def test_gm_self_picks_always_blocked(self) -> None:
        session = MagicMock()
        draft = MagicMock(
            id=1,
            status="live",
            awaiting_admin_resolution=False,
            expansion_pick_cooldown_active=False,
            league_slug="bowl-cap",
            current_slot_index=0,
        )
        slot = MagicMock(
            overall_pick=1,
            team_id=10,
            round=1,
            phase="goalie",
            forfeited=False,
        )
        player = MagicMock(id=123)

        session.get.return_value = player

        with (
            patch.object(expansion_draft_state, "slots_ordered", return_value=[slot]),
            patch.object(expansion_draft_state, "_pick_row_for_overall", return_value=None),
            patch.object(expansion_draft_state, "validate_pick", return_value=None),
        ):
            msg = expansion_draft_state.record_pick(
                session,
                draft,
                player_id=123,
                user_id=456,
                source="gm",
            )

        self.assertEqual(msg, expansion_draft_state.GM_SELF_PICK_DISABLED_MSG)

    def test_skip_current_slot_forfeits_and_advances(self) -> None:
        session = MagicMock()
        draft = MagicMock(
            id=1,
            status="live",
            current_slot_index=0,
            awaiting_admin_resolution=False,
        )
        slot = MagicMock(
            overall_pick=1,
            team_id=10,
            round=1,
            phase="goalie",
            forfeited=False,
            notes=None,
        )
        with (
            patch.object(expansion_draft_state, "slots_ordered", side_effect=[[slot], [slot]]),
            patch.object(expansion_draft_state, "_pick_row_for_overall", return_value=None),
            patch.object(expansion_draft_state, "sync_current_slot_and_clock"),
            patch.object(expansion_draft_state, "_finalize_if_done"),
            patch.object(expansion_draft_state, "invalidate_expansion_eligible_cache"),
        ):
            err = expansion_draft_state.skip_current_slot(session, draft, admin_user_id=99)
        self.assertIsNone(err)
        self.assertTrue(slot.forfeited)
        self.assertEqual(slot.notes, expansion_draft_state.ADMIN_SKIP_NOTE)
        self.assertEqual(draft.current_slot_index, 1)

    def test_undo_last_admin_skip_restores_slot(self) -> None:
        session = MagicMock()
        draft = MagicMock(id=1, status="live", current_slot_index=1)
        slot = MagicMock(
            overall_pick=1,
            forfeited=True,
            notes=expansion_draft_state.ADMIN_SKIP_NOTE,
        )
        session.scalar.return_value = None
        with (
            patch.object(expansion_draft_state, "slots_ordered", return_value=[slot]),
            patch.object(expansion_draft_state, "sync_current_slot_and_clock"),
            patch.object(expansion_draft_state, "invalidate_expansion_eligible_cache"),
        ):
            err = expansion_draft_state.undo_last_admin_skip(session, draft)
        self.assertIsNone(err)
        self.assertFalse(slot.forfeited)
        self.assertIsNone(slot.notes)
        self.assertEqual(draft.current_slot_index, 0)

    def test_undo_last_pick_requires_existing_pick(self) -> None:
        session = MagicMock()
        draft = MagicMock(id=1, status="live")
        session.scalars.return_value.all.return_value = []
        err = expansion_draft_state.undo_last_pick(session, draft)
        self.assertEqual(err, "No picks to undo.")

    def test_undo_pick_form_post_redirects_with_flash(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        draft = MagicMock(id=7, status="live")
        with (
            patch("app.routes.expansion_draft_hub.featured_expansion_draft", return_value=draft),
            patch("app.routes.expansion_draft_hub.undo_last_pick", return_value=None) as undo_mock,
            patch("app.routes.expansion_draft_hub.league_hub_staff", return_value=True),
            patch("app.routes.expansion_draft_hub._expansion_hub_allowed", return_value=True),
            patch("app.routes.expansion_draft_hub.commit_with_sqlite_retry"),
        ):
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["_user_id"] = "1"
                    sess["_fresh"] = True
                page = client.get("/expansion-draft-hub")
                self.assertEqual(page.status_code, 200)
                token = _csrf_token(page.get_data(as_text=True))
                self.assertTrue(token)
                resp = client.post(
                    "/expansion-draft-hub/admin/undo-pick",
                    data={"csrf_token": token},
                    follow_redirects=True,
                )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Last pick removed.", resp.data)
        undo_mock.assert_called_once()

    def test_skip_pick_form_post_redirects_with_flash(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        draft = MagicMock(id=7, status="live")
        with (
            patch("app.routes.expansion_draft_hub.featured_expansion_draft", return_value=draft),
            patch("app.routes.expansion_draft_hub.skip_current_slot", return_value=None) as skip_mock,
            patch("app.routes.expansion_draft_hub.league_hub_staff", return_value=True),
            patch("app.routes.expansion_draft_hub._expansion_hub_allowed", return_value=True),
            patch("app.routes.expansion_draft_hub.commit_with_sqlite_retry"),
        ):
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["_user_id"] = "1"
                    sess["_fresh"] = True
                page = client.get("/expansion-draft-hub")
                self.assertEqual(page.status_code, 200)
                token = _csrf_token(page.get_data(as_text=True))
                self.assertTrue(token)
                resp = client.post(
                    "/expansion-draft-hub/admin/skip-pick",
                    data={"csrf_token": token},
                    follow_redirects=True,
                )
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Current pick skipped", resp.data)
        skip_mock.assert_called_once()

    def test_admin_apply_live_order_post_redirects(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        app.config["WTF_CSRF_ENABLED"] = False
        draft = MagicMock(id=7, status="live", league_slug="bowl-cap")
        with (
            patch("app.routes.site_portal.require_admin_role"),
            patch("app.routes.site_portal.db") as db_mock,
            patch(
                "app.services.expansion_draft_state.apply_live_expansion_order",
                return_value=(4, None),
            ) as apply_mock,
            patch("app.routes.site_portal.commit_with_sqlite_retry"),
        ):
            db_mock.session.get.return_value = draft
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["_user_id"] = "1"
                    sess["_fresh"] = True
                resp = client.post(
                    "/admin/expansion-draft-hub/7",
                    data={
                        "action": "apply_live_order",
                        "phase_order_format": "serpentine",
                        "goalie_phase_first_team_id": "12",
                        "skater_phase_first_team_id": "5",
                    },
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/expansion-draft-hub/7", resp.headers.get("Location", ""))
        apply_mock.assert_called_once()
        kwargs = apply_mock.call_args.kwargs
        self.assertEqual(kwargs["order_fmt"], "serpentine")
        self.assertEqual(kwargs["goalie_first"], 12)
        self.assertEqual(kwargs["skater_first"], 5)

    def test_admin_save_live_slot_teams_post_redirects(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        app.config["WTF_CSRF_ENABLED"] = False
        draft = MagicMock(id=7, status="live", league_slug="bowl-cap")
        with (
            patch("app.routes.site_portal.require_admin_role"),
            patch("app.routes.site_portal.db") as db_mock,
            patch(
                "app.services.expansion_draft_state.reassign_unpicked_expansion_slots",
                return_value=(2, None),
            ) as reassign_mock,
            patch("app.routes.site_portal.commit_with_sqlite_retry"),
        ):
            db_mock.session.get.return_value = draft
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["_user_id"] = "1"
                    sess["_fresh"] = True
                resp = client.post(
                    "/admin/expansion-draft-hub/7",
                    data={
                        "action": "save_live_slot_teams",
                        "slot_team_1": "12",
                        "slot_team_2": "5",
                    },
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/expansion-draft-hub/7", resp.headers.get("Location", ""))
        reassign_mock.assert_called_once()
        self.assertEqual(reassign_mock.call_args.args[2], {1: 12, 2: 5})

    def test_live_admin_template_includes_order_controls(self) -> None:
        from flask import render_template

        app = create_app(make_league_config("bowl-historical"))
        team_a = SimpleNamespace(id=5)
        team_a.full_display_name = lambda: "New York Islanders"
        team_b = SimpleNamespace(id=12)
        team_b.full_display_name = lambda: "Atlanta Flames"
        draft = SimpleNamespace(
            id=9,
            name="1972 BOWL Expansion Draft",
            status="live",
            phase_order_format="straight",
            serpentine_continuous=False,
            goalie_phase_first_team_id=5,
            skater_phase_first_team_id=12,
            current_slot_index=0,
        )
        slots = [
            SimpleNamespace(overall_pick=1, round=1, phase="goalie", team_id=5, forfeited=False),
            SimpleNamespace(overall_pick=2, round=1, phase="goalie", team_id=12, forfeited=False),
        ]
        with app.test_request_context("/admin/expansion-draft-hub/9"):
            html = render_template(
                "admin_expansion_draft_hub_edit.html",
                draft=draft,
                main_teams=[],
                exempt_ids=set(),
                eligible_ids=set(),
                expansion_org_players={},
                expansion_franchise_ids=[5, 12],
                expansion_franchise_teams=[team_a, team_b],
                expansion_team_label_by_id={5: "New York Islanders", 12: "Atlanta Flames"},
                picked_overalls=set(),
                slots=slots,
                sched_value="",
            )
        self.assertIn("Draft order", html)
        self.assertIn('name="action" value="apply_live_order"', html)
        self.assertIn('name="action" value="save_live_slot_teams"', html)
        self.assertIn("On the clock", html)
        self.assertIn("Save remaining pick order", html)


if __name__ == "__main__":
    unittest.main()
