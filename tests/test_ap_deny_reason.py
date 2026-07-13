"""AP redemption deny requires an admin reason (shared across all league mounts)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services.gm_notifications import notify_redemption_denied


class ApDenyReasonTest(unittest.TestCase):
    def test_notify_redemption_denied_includes_reason(self) -> None:
        req = SimpleNamespace(id=42, user_id=7, admin_note="  Duplicate request  ")
        with patch("app.services.gm_notifications._add_notification") as add_mock:
            with patch("app.services.gm_notifications._commit_notifications"):
                notify_redemption_denied("bowl-historical", req)
        note = add_mock.call_args[0][0]
        self.assertEqual(note.kind, "redemption_denied")
        self.assertIn("Reason: Duplicate request", note.body)

    def test_admin_ap_deny_rejects_empty_reason(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        app.config["WTF_CSRF_ENABLED"] = False
        req = MagicMock(id=9, league_slug="bowl-cap", status="pending", admin_note="")
        with (
            patch("app.routes.site_portal.require_admin_role"),
            patch("app.routes.site_portal.db") as db_mock,
            patch("app.routes.site_portal.notify_redemption_denied") as notify_mock,
            patch("app.routes.site_portal.commit_with_sqlite_retry"),
        ):
            db_mock.session.get.return_value = req
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["_user_id"] = "1"
                    sess["_fresh"] = True
                resp = client.post(
                    "/admin/ap-requests/9/deny",
                    data={"admin_note": "   "},
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/ap-requests/9", resp.headers.get("Location", ""))
        self.assertEqual(req.status, "pending")
        notify_mock.assert_not_called()

    def test_admin_ap_deny_saves_reason_and_notifies(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["WTF_CSRF_ENABLED"] = False
        req = MagicMock(id=11, league_slug="bowl-fantasy", status="pending", admin_note="")
        with (
            patch("app.routes.site_portal.require_admin_role"),
            patch("app.routes.site_portal.db") as db_mock,
            patch("app.routes.site_portal.notify_redemption_denied") as notify_mock,
            patch("app.routes.site_portal.commit_with_sqlite_retry"),
        ):
            db_mock.session.get.return_value = req
            with app.test_client() as client:
                with client.session_transaction() as sess:
                    sess["_user_id"] = "1"
                    sess["_fresh"] = True
                resp = client.post(
                    "/admin/ap-requests/11/deny",
                    data={"admin_note": "Not eligible this week"},
                    follow_redirects=False,
                )
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/admin/ap-requests", resp.headers.get("Location", ""))
        self.assertEqual(req.status, "denied")
        self.assertEqual(req.admin_note, "Not eligible this week")
        self.assertIsNotNone(req.processed_at)
        notify_mock.assert_called_once_with("bowl-fantasy", req)


if __name__ == "__main__":
    unittest.main()
