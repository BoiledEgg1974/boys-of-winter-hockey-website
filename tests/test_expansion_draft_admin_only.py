"""Expansion draft picks are commissioner-only (matches entry Draft Hub default)."""

from __future__ import annotations

import re
import unittest
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


if __name__ == "__main__":
    unittest.main()
