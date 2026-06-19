"""Staff budget panel helpers, hire budget gate, and payroll on approval."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.league_finances import (
    STAFF_HIRE_INSUFFICIENT_FUNDS_MSG,
    can_afford_staff_hire,
    default_salary_for_role,
    effective_staff_payroll,
    estimated_roster_payroll,
)
from app.services.staff_salaries import StaffDefaultSalaries, compute_staff_default_salaries
from app.services.staff_transactions import (
    _projected_payroll_after_hire,
    approve_staff_request,
    submit_hire_request,
)
from app.site_models import StaffChangeRequest, TeamStaffBudget


def _defaults() -> StaffDefaultSalaries:
    return StaffDefaultSalaries(
        head_coach=424_919,
        assistant_coaches=303_514,
        scouts=60_703,
        trainer=121_406,
    )


class StaffBudgetHelpersTest(unittest.TestCase):
    def test_default_salary_for_role(self) -> None:
        d = _defaults()
        self.assertEqual(default_salary_for_role("head_coach", d), 424_919)
        self.assertEqual(default_salary_for_role("assistant_coach", d), 303_514)
        self.assertEqual(default_salary_for_role("scout", d), 60_703)
        self.assertEqual(default_salary_for_role("trainer", d), 121_406)

    def test_effective_staff_payroll_manual_wins(self) -> None:
        roster = [SimpleNamespace(role="head_coach")]
        payroll, manual = effective_staff_payroll(
            current_salary_amount=500_000,
            roster=roster,
            defaults=_defaults(),
        )
        self.assertEqual(payroll, 500_000)
        self.assertTrue(manual)

    def test_effective_staff_payroll_estimated_when_manual_zero(self) -> None:
        roster = [
            SimpleNamespace(role="head_coach"),
            SimpleNamespace(role="scout"),
        ]
        payroll, manual = effective_staff_payroll(
            current_salary_amount=0,
            roster=roster,
            defaults=_defaults(),
        )
        self.assertEqual(payroll, 424_919 + 60_703)
        self.assertFalse(manual)

    def test_can_afford_staff_hire_with_pending(self) -> None:
        fin = {
            "defaults": _defaults(),
            "available_for_hire": 100_000,
        }
        self.assertFalse(can_afford_staff_hire(fin, "head_coach"))
        self.assertTrue(can_afford_staff_hire(fin, "scout"))

    def test_projected_payroll_after_hire_bootstraps_estimate(self) -> None:
        roster = [SimpleNamespace(role="scout")]
        projected = _projected_payroll_after_hire(
            current_salary_amount=0,
            roster_before=roster,
            role="trainer",
            defaults=_defaults(),
        )
        self.assertEqual(projected, 60_703 + 121_406)

    def test_projected_payroll_after_hire_increments_manual(self) -> None:
        projected = _projected_payroll_after_hire(
            current_salary_amount=1_000_000,
            roster_before=[],
            role="scout",
            defaults=_defaults(),
        )
        self.assertEqual(projected, 1_000_000 + 60_703)

    def test_compute_staff_default_salaries_formula(self) -> None:
        d = compute_staff_default_salaries(100_000_000, 10)
        assert d is not None
        base = 10_000_000 / 62.0
        self.assertEqual(d.scouts, round(base))
        self.assertEqual(d.head_coach, round(base * 7))
        self.assertEqual(d.trainer, round(base * 2))


class SubmitHireBudgetGateTest(unittest.TestCase):
    @patch("app.services.league_finances.staff_finances_for_team")
    @patch("app.services.staff_transactions._active_roster_entry", return_value=None)
    @patch("app.services.staff_transactions.hire_limit_status")
    @patch("app.services.staff_transactions.get_staff_profile")
    def test_submit_hire_blocks_insufficient_budget(
        self,
        mock_profile: MagicMock,
        mock_limit: MagicMock,
        _mock_active: MagicMock,
        mock_finances: MagicMock,
    ) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        mock_limit.return_value = MagicMock(limit_reached=False)
        mock_profile.return_value = {"staff_fhm_id": "99", "full_name": "Coach", "fhm_team_id": ""}
        mock_finances.return_value = {
            "defaults": _defaults(),
            "available_for_hire": 0,
        }

        result = submit_hire_request(
            session,
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            user_id=2,
            staff_fhm_id="99",
            role="head_coach",
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.message, STAFF_HIRE_INSUFFICIENT_FUNDS_MSG)
        session.add.assert_not_called()


class ApproveStaffPayrollTest(unittest.TestCase):
    @patch("app.services.staff_transactions._apply_hire_payroll_increment")
    @patch("app.services.staff_transactions._get_or_create_team_staff_budget")
    @patch("app.services.staff_transactions._league_staff_defaults", return_value=_defaults())
    @patch("app.services.staff_transactions.active_roster_for_team", return_value=[])
    @patch("app.services.staff_transactions._active_roster_entry", return_value=None)
    def test_approve_hire_increments_payroll(
        self,
        _mock_active: MagicMock,
        _mock_roster: MagicMock,
        _mock_defaults: MagicMock,
        mock_budget_row: MagicMock,
        mock_apply: MagicMock,
    ) -> None:
        session = MagicMock()
        budget = TeamStaffBudget(
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            budget_amount=5_000_000,
            current_salary_amount=0,
        )
        mock_budget_row.return_value = budget
        req = StaffChangeRequest(
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            user_id=2,
            request_type="hire",
            role="scout",
            staff_fhm_id="55",
            staff_name="Scout Name",
            status="pending",
        )
        req.id = 7

        result = approve_staff_request(session, req, admin_user_id=9)

        self.assertTrue(result.ok)
        mock_apply.assert_called_once()
        session.add.assert_called()

    @patch("app.services.staff_transactions._get_or_create_team_staff_budget")
    @patch("app.services.staff_transactions._league_staff_defaults", return_value=_defaults())
    @patch("app.services.staff_transactions.active_roster_for_team", return_value=[])
    @patch("app.services.staff_transactions._active_roster_entry", return_value=None)
    def test_approve_hire_denied_when_over_budget(
        self,
        _mock_active: MagicMock,
        _mock_roster: MagicMock,
        _mock_defaults: MagicMock,
        mock_budget_row: MagicMock,
    ) -> None:
        session = MagicMock()
        budget = TeamStaffBudget(
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            budget_amount=50_000,
            current_salary_amount=0,
        )
        mock_budget_row.return_value = budget
        req = StaffChangeRequest(
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            user_id=2,
            request_type="hire",
            role="head_coach",
            staff_fhm_id="55",
            staff_name="Coach Name",
            status="pending",
        )

        result = approve_staff_request(session, req, admin_user_id=9)

        self.assertFalse(result.ok)
        self.assertEqual(req.status, "denied")

    def test_approve_fire_does_not_touch_budget_row(self) -> None:
        session = MagicMock()
        entry = SimpleNamespace(fired_at=None)
        session.scalar.return_value = entry
        req = StaffChangeRequest(
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            user_id=2,
            request_type="fire",
            role="scout",
            staff_fhm_id="55",
            staff_name="Scout Name",
            status="pending",
        )

        with patch(
            "app.services.staff_transactions._get_or_create_team_staff_budget"
        ) as mock_budget:
            result = approve_staff_request(session, req, admin_user_id=9)
            mock_budget.assert_not_called()

        self.assertTrue(result.ok)
        self.assertIsNotNone(entry.fired_at)


class FirePayrollNotifyTest(unittest.TestCase):
    @patch("app.services.admin_review_notify.queue_site_admin_in_app_notifications")
    @patch("app.services.admin_review_notify.try_send_admin_review_email")
    @patch("app.services.admin_review_notify._abs_url_for", return_value="/admin/staff-budgets")
    def test_notify_staff_fire_payroll_adjustment(
        self,
        _mock_url: MagicMock,
        mock_email: MagicMock,
        mock_queue: MagicMock,
    ) -> None:
        from app.services.admin_review_notify import notify_staff_fire_payroll_adjustment

        notify_staff_fire_payroll_adjustment(
            league_slug="bowl-historical",
            league_display_name="BOWL-Historical",
            request_id=42,
            team_name="Detroit Red Wings",
            staff_name="Pat Burns",
            role_label="Head Coach",
            suggested_reduction=424_919,
        )

        mock_email.assert_called_once()
        mock_queue.assert_called_once()
        kwargs = mock_queue.call_args.kwargs
        self.assertEqual(kwargs["kind"], "admin_staff_payroll_adjust")
        self.assertIn("424,919", kwargs["body"])


if __name__ == "__main__":
    unittest.main()
