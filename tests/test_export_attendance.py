"""Export attendance tracker service, route, and template marker tests."""
from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.export_attendance import (
    ATTENDANCE_WINDOW_DAYS,
    GAP_WARNING_THRESHOLD_DAYS,
    build_attendance_tracker_payload,
    export_gap_warning_body,
    maybe_send_export_gap_warning,
    parse_export_date,
    register_export_attendance,
    rolling_attendance_window_dates,
)


class ExportAttendanceServiceTest(unittest.TestCase):
    def test_rolling_window_newest_first(self) -> None:
        anchor = date(2026, 6, 9)
        dates = rolling_attendance_window_dates(anchor=anchor, days=5)
        self.assertEqual(len(dates), 5)
        self.assertEqual(dates[0], anchor)
        self.assertEqual(dates[-1], date(2026, 6, 5))

    def test_parse_export_date_defaults_to_today(self) -> None:
        default = date(2026, 1, 2)
        self.assertEqual(parse_export_date("", default=default), default)
        self.assertEqual(parse_export_date("2026-03-15", default=default), date(2026, 3, 15))
        self.assertEqual(parse_export_date("bad", default=default), default)

    def test_export_gap_warning_body_fills_placeholders(self) -> None:
        body = export_gap_warning_body(gm_name="Pat GM", league_slug="bowl-cap")
        self.assertIn("Hello Pat GM.", body)
        self.assertIn("participation in bowl-cap.", body)
        self.assertIn("message the Commissioner", body)

    def test_register_export_attendance_is_idempotent(self) -> None:
        session = MagicMock()
        existing = SimpleNamespace(
            ap_ledger_entry_id=None,
            gap_days=None,
            gap_warning_sent_at=None,
        )
        session.scalar.return_value = existing
        row, created = register_export_attendance(
            session,
            league_slug="bowl-cap",
            team_id=10,
            export_date=date(2026, 6, 9),
            checked_by_user_id=1,
            ap_ledger_entry_id=99,
        )
        self.assertIs(row, existing)
        self.assertFalse(created)
        self.assertEqual(existing.ap_ledger_entry_id, 99)
        session.add.assert_not_called()

    def test_register_export_attendance_computes_gap_days(self) -> None:
        session = MagicMock()
        session.scalar.side_effect = [None, date(2026, 5, 20)]

        def _add(row):
            row.id = 7

        session.add.side_effect = lambda row: _add(row)
        session.flush = MagicMock()

        row, created = register_export_attendance(
            session,
            league_slug="bowl-cap",
            team_id=10,
            export_date=date(2026, 6, 9),
            checked_by_user_id=1,
        )
        self.assertTrue(created)
        self.assertEqual(row.gap_days, 20)
        self.assertEqual(row.previous_export_date, date(2026, 5, 20))

    def test_maybe_send_export_gap_warning_skips_small_gap(self) -> None:
        session = MagicMock()
        row = SimpleNamespace(gap_warning_sent_at=None, gap_days=5, team_id=10)
        sent = maybe_send_export_gap_warning(
            session,
            attendance_row=row,
            league_slug="bowl-cap",
            admin_user_id=3,
        )
        self.assertFalse(sent)

    def test_maybe_send_export_gap_warning_sends_once(self) -> None:
        session = MagicMock()
        row = SimpleNamespace(gap_warning_sent_at=None, gap_days=12, team_id=10)
        mem = SimpleNamespace(user_id=42)
        gm_user = SimpleNamespace(discord_name="Alex", username="", email="")
        session.scalar.return_value = mem
        session.get.return_value = gm_user

        with patch("app.services.export_attendance.create_gm_message") as msg:
            sent = maybe_send_export_gap_warning(
                session,
                attendance_row=row,
                league_slug="bowl-cap",
                admin_user_id=3,
            )

        self.assertTrue(sent)
        self.assertIsNotNone(row.gap_warning_sent_at)
        msg.assert_called_once()
        kwargs = msg.call_args.kwargs
        self.assertEqual(kwargs["to_user_id"], 42)
        self.assertEqual(kwargs["event_key"], "export_gap_warning")
        self.assertIn("bowl-cap", kwargs["body"])

    def test_build_attendance_tracker_payload_shapes_rows(self) -> None:
        anchor = date(2026, 6, 9)
        mem = SimpleNamespace(team_id=10, user_id=5)
        team = SimpleNamespace(id=10, full_display_name=lambda: "Toronto")
        gm_user = SimpleNamespace(discord_name="GM One", username="", email="")
        attendance = SimpleNamespace(team_id=10, export_date=anchor)

        session = MagicMock()
        session.scalars.side_effect = [
            MagicMock(all=MagicMock(return_value=[mem])),
            MagicMock(all=MagicMock(return_value=[team])),
            MagicMock(all=MagicMock(return_value=[attendance])),
        ]
        session.get.return_value = gm_user
        session.scalar.return_value = anchor

        payload = build_attendance_tracker_payload(
            session,
            "bowl-cap",
            anchor=anchor,
            logo_resolver=lambda t: f"/logo/{t.id}",
        )
        self.assertEqual(payload["window_days"], ATTENDANCE_WINDOW_DAYS)
        self.assertEqual(payload["gap_threshold_days"], GAP_WARNING_THRESHOLD_DAYS)
        self.assertEqual(len(payload["dates"]), ATTENDANCE_WINDOW_DAYS)
        self.assertEqual(payload["dates"][0], anchor.isoformat())
        self.assertEqual(len(payload["rows"]), 1)
        row = payload["rows"][0]
        self.assertEqual(row["team_name"], "Toronto")
        self.assertEqual(row["gm_name"], "GM One")
        self.assertEqual(row["total_exports"], 1)
        self.assertEqual(row["current_gap_days"], 0)
        self.assertFalse(row["gap_warning"])
        self.assertTrue(row["cells"][0]["exported"])
        self.assertFalse(row["cells"][1]["exported"])


class ExportAttendanceTemplateTest(unittest.TestCase):
    def test_route_template_nav_and_export_flow_markers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "export_attendance_tracker.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        portal = (root / "app" / "routes" / "site_portal.py").read_text(encoding="utf-8")
        base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        ledger = (root / "app" / "templates" / "admin_ap_ledger.html").read_text(encoding="utf-8")
        models = (root / "app" / "site_models.py").read_text(encoding="utf-8")
        db_utils = (root / "app" / "db_utils.py").read_text(encoding="utf-8")

        for marker in (
            "Attendance Tracker",
            "export-attendance-tracker__grid",
            "export-attendance-tracker__cell--yes",
            "export-attendance-tracker__cell--no",
            "tracker.window_days",
            "tracker.gap_threshold_days",
        ):
            self.assertIn(marker, template)

        self.assertIn("def export_attendance_tracker", portal)
        self.assertIn('"/attendance-tracker"', portal)
        self.assertIn("register_export_attendance", portal)
        self.assertIn("maybe_send_export_gap_warning", portal)
        self.assertIn("parse_export_date", portal)
        self.assertIn("source_ref=f\"manual_export:", portal)
        self.assertIn("ap_ledger_entry_id=ap_ledger_entry_id", portal)
        self.assertIn("getattr(ledger_row, \"id\", None) is not None", portal)
        self.assertIn("write_with_sqlite_retry", portal)
        self.assertIn("site_gm.export_attendance_tracker", base)
        self.assertIn("Attendance Tracker", base)
        self.assertIn('name="export_date"', ledger)
        self.assertIn("class GmExportAttendance", models)
        self.assertIn("ensure_gm_export_attendance_sqlite", db_utils)
        self.assertIn(".export-attendance-tracker__grid", css)
        self.assertIn(".ap-export-dialog__date-label", css)


if __name__ == "__main__":
    unittest.main()
