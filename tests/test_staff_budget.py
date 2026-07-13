"""Staff budget helpers, contract payroll, and admin hire/fire."""
from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.league_finances import (
    STAFF_HIRE_INSUFFICIENT_FUNDS_MSG,
    can_afford_staff_hire,
    contract_roster_payroll,
    default_salary_for_role,
    effective_staff_payroll,
    severance_payroll,
)
from app.services.staff_salaries import StaffDefaultSalaries, compute_staff_default_salaries
from app.services.staff_transactions import (
    _active_roster_entry,
    _entry_claims_staff,
    admin_fire_staff,
    admin_hire_staff,
    admin_save_staff_contract,
    contract_active,
    contract_end_season_year,
    expire_stale_staff_contracts,
)
from app.site_models import TeamStaffRosterEntry


def _defaults() -> StaffDefaultSalaries:
    return StaffDefaultSalaries(
        head_coach=424_919,
        assistant_coaches=303_514,
        scouts=60_703,
        trainer=121_406,
    )


def _entry(**kwargs) -> TeamStaffRosterEntry:
    row = TeamStaffRosterEntry(
        league_slug="bowl-historical",
        season_start_year=1968,
        team_id=1,
        staff_fhm_id="55",
        staff_name="Test Staff",
        role="scout",
        annual_salary=60_703,
        contract_years=2,
        contract_start_season_year=1968,
        hired_at=datetime.utcnow(),
    )
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


class StaffBudgetHelpersTest(unittest.TestCase):
    def test_default_salary_for_role(self) -> None:
        d = _defaults()
        self.assertEqual(default_salary_for_role("head_coach", d), 424_919)
        self.assertEqual(default_salary_for_role("assistant_coach", d), 303_514)
        self.assertEqual(default_salary_for_role("scout", d), 60_703)
        self.assertEqual(default_salary_for_role("trainer", d), 121_406)
        self.assertEqual(default_salary_for_role("team_owner", d), 0)
        self.assertEqual(default_salary_for_role("general_manager", d), 0)

    def test_contract_end_season_year(self) -> None:
        row = _entry(contract_start_season_year=1968, contract_years=3)
        self.assertEqual(contract_end_season_year(row), 1970)

    def test_contract_active_and_expired(self) -> None:
        row = _entry(contract_start_season_year=1968, contract_years=2)
        self.assertTrue(contract_active(row, 1968))
        self.assertTrue(contract_active(row, 1969))
        self.assertFalse(contract_active(row, 1970))

    def test_contract_roster_payroll_sums_salaries(self) -> None:
        roster = [
            _entry(annual_salary=100),
            _entry(staff_fhm_id="56", annual_salary=200, contract_start_season_year=1968, contract_years=1),
        ]
        self.assertEqual(contract_roster_payroll(roster, 1968), 300)

    def test_effective_staff_payroll_includes_severance(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = [50_000, 25_000]
        roster = [_entry(annual_salary=60_703)]
        payroll, contract_pay, severance = effective_staff_payroll(
            roster=roster,
            season_start_year=1968,
            session=session,
            league_slug="bowl-historical",
            team_id=1,
        )
        self.assertEqual(contract_pay, 60_703)
        self.assertEqual(severance, 75_000)
        self.assertEqual(payroll, 135_703)

    def test_severance_payroll(self) -> None:
        session = MagicMock()
        session.scalars.return_value.all.return_value = [10_000, 5_000]
        self.assertEqual(
            severance_payroll(
                session,
                league_slug="bowl-historical",
                team_id=1,
                season_start_year=1968,
            ),
            15_000,
        )

    def test_can_afford_staff_hire(self) -> None:
        fin = {
            "defaults": _defaults(),
            "available_for_hire": 100_000,
        }
        self.assertFalse(can_afford_staff_hire(fin, "head_coach"))
        self.assertTrue(can_afford_staff_hire(fin, "scout"))

    def test_compute_staff_default_salaries_formula(self) -> None:
        d = compute_staff_default_salaries(100_000_000, 10)
        assert d is not None
        base = 10_000_000 / 62.0
        self.assertEqual(d.scouts, round(base))
        self.assertEqual(d.head_coach, round(base * 7))
        self.assertEqual(d.trainer, round(base * 2))


class AdminStaffActionsTest(unittest.TestCase):
    @patch("app.services.staff_transactions._validate_hire_budget", return_value=None)
    @patch("app.services.staff_transactions._league_staff_defaults", return_value=_defaults())
    @patch("app.services.staff_transactions._active_roster_entry", return_value=None)
    @patch("app.services.staff_transactions.get_staff_profile")
    def test_admin_hire_staff_creates_contract(
        self,
        mock_profile: MagicMock,
        _mock_active: MagicMock,
        _mock_defaults: MagicMock,
        _mock_budget: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_profile.return_value = {"staff_fhm_id": "99", "full_name": "Coach", "fhm_team_id": ""}

        result = admin_hire_staff(
            session,
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            admin_user_id=2,
            staff_fhm_id="99",
            role="head_coach",
            contract_years=3,
        )

        self.assertTrue(result.ok)
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        self.assertEqual(added.annual_salary, 424_919)
        self.assertEqual(added.contract_years, 3)

    @patch("app.services.staff_transactions._validate_hire_budget")
    @patch("app.services.staff_transactions._league_staff_defaults", return_value=_defaults())
    @patch("app.services.staff_transactions._active_roster_entry", return_value=None)
    @patch("app.services.staff_transactions.get_staff_profile")
    def test_admin_hire_blocks_insufficient_budget(
        self,
        mock_profile: MagicMock,
        _mock_active: MagicMock,
        _mock_defaults: MagicMock,
        mock_budget: MagicMock,
    ) -> None:
        session = MagicMock()
        mock_profile.return_value = {"staff_fhm_id": "99", "full_name": "Coach", "fhm_team_id": ""}
        mock_budget.return_value = STAFF_HIRE_INSUFFICIENT_FUNDS_MSG

        result = admin_hire_staff(
            session,
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            admin_user_id=2,
            staff_fhm_id="99",
            role="head_coach",
            contract_years=1,
        )

        self.assertFalse(result.ok)
        session.add.assert_not_called()

    def test_admin_fire_staff_adds_severance(self) -> None:
        session = MagicMock()
        entry = _entry()
        session.scalar.return_value = entry

        result = admin_fire_staff(
            session,
            league_slug="bowl-historical",
            season_start_year=1968,
            team_id=1,
            admin_user_id=2,
            staff_fhm_id="55",
            penalty_amount=100_000,
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(entry.fired_at)
        self.assertEqual(session.add.call_count, 1)
        severance = session.add.call_args[0][0]
        self.assertEqual(severance.penalty_amount, 100_000)


class AdminSaveStaffContractOrphansTest(unittest.TestCase):
    def test_empty_placeholder_does_not_claim_staff(self) -> None:
        ghost = _entry(
            team_id=9,
            annual_salary=0,
            contract_start_season_year=0,
            contract_years=1,
        )
        self.assertFalse(_entry_claims_staff(ghost, 1968))
        self.assertFalse(contract_active(ghost, 1968))

    def test_active_roster_entry_ignores_empty_placeholders(self) -> None:
        ghost = _entry(
            team_id=9,
            staff_fhm_id="77",
            annual_salary=0,
            contract_start_season_year=0,
            contract_years=1,
        )
        session = MagicMock()
        session.scalars.return_value.all.return_value = [ghost]
        self.assertIsNone(
            _active_roster_entry(
                session,
                league_slug="bowl-cap",
                staff_fhm_id="77",
                season_start_year=1968,
            )
        )

    def test_expire_stale_clears_prior_season_rows(self) -> None:
        stale = _entry(
            season_start_year=1967,
            contract_start_season_year=1967,
            contract_years=1,
            annual_salary=50_000,
        )
        session = MagicMock()
        session.scalars.return_value.all.return_value = [stale]
        expired = expire_stale_staff_contracts(
            session, league_slug="bowl-cap", season_start_year=1968
        )
        self.assertEqual(expired, 1)
        self.assertIsNotNone(stale.fired_at)

    @patch("app.services.league_finances.severance_payroll", return_value=0)
    @patch("app.services.staff_transactions.active_roster_for_team", return_value=[])
    @patch("app.services.staff_transactions._get_or_create_team_staff_budget")
    @patch("app.services.staff_transactions.list_staff_profiles_for_fhm_team", return_value=[])
    @patch("app.services.staff_transactions.get_staff_profile")
    def test_save_releases_orphan_contract_when_staff_moved_in_fhm(
        self,
        mock_profile: MagicMock,
        _mock_profiles: MagicMock,
        mock_budget: MagicMock,
        _mock_roster: MagicMock,
        _mock_severance: MagicMock,
    ) -> None:
        orphan = _entry(
            team_id=9,
            staff_fhm_id="77",
            staff_name="Charlie Burns",
            annual_salary=80_000,
            contract_start_season_year=1968,
            contract_years=2,
        )
        session = MagicMock()
        # First scalar: no entry on Anaheim (team 1); open-rows via scalars().all()
        session.scalar.return_value = None
        session.scalars.return_value.all.return_value = [orphan]
        mock_profile.return_value = {
            "staff_fhm_id": "77",
            "full_name": "Charlie Burns",
            "fhm_team_id": "24",
        }
        mock_budget.return_value = SimpleNamespace(budget_amount=0)

        result = admin_save_staff_contract(
            session,
            league_slug="bowl-cap",
            season_start_year=1968,
            team_id=1,
            staff_fhm_id="77",
            role="scout",
            annual_salary=90_000,
            contract_years=1,
            fhm_team_id="24",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(orphan.fired_at)
        session.add.assert_called_once()
        added = session.add.call_args[0][0]
        self.assertEqual(added.team_id, 1)
        self.assertEqual(added.annual_salary, 90_000)

    @patch("app.services.staff_transactions.get_staff_profile")
    def test_save_still_blocks_true_cross_team_contract(
        self,
        mock_profile: MagicMock,
    ) -> None:
        other = _entry(
            team_id=9,
            staff_fhm_id="77",
            annual_salary=80_000,
            contract_start_season_year=1968,
            contract_years=2,
        )
        session = MagicMock()
        session.scalar.return_value = None
        session.scalars.return_value.all.return_value = [other]
        mock_profile.return_value = {
            "staff_fhm_id": "77",
            "full_name": "Charlie Burns",
            "fhm_team_id": "99",
        }

        result = admin_save_staff_contract(
            session,
            league_slug="bowl-cap",
            season_start_year=1968,
            team_id=1,
            staff_fhm_id="77",
            role="scout",
            annual_salary=90_000,
            contract_years=1,
            fhm_team_id="24",
        )

        self.assertFalse(result.ok)
        self.assertIn("another team", result.message)
        self.assertIsNone(other.fired_at)
        session.add.assert_not_called()

    @patch("app.services.league_finances.severance_payroll", return_value=0)
    @patch("app.services.staff_transactions.active_roster_for_team", return_value=[])
    @patch("app.services.staff_transactions._get_or_create_team_staff_budget")
    @patch("app.services.staff_transactions.list_staff_profiles_for_fhm_team", return_value=[])
    @patch("app.services.staff_transactions.get_staff_profile")
    def test_save_ignores_empty_ghost_on_other_team(
        self,
        mock_profile: MagicMock,
        _mock_profiles: MagicMock,
        mock_budget: MagicMock,
        _mock_roster: MagicMock,
        _mock_severance: MagicMock,
    ) -> None:
        ghost = _entry(
            team_id=9,
            staff_fhm_id="77",
            annual_salary=0,
            contract_start_season_year=0,
            contract_years=1,
        )
        session = MagicMock()
        session.scalar.return_value = None
        session.scalars.return_value.all.return_value = [ghost]
        mock_profile.return_value = {
            "staff_fhm_id": "77",
            "full_name": "Charlie Burns",
            "fhm_team_id": "24",
        }
        mock_budget.return_value = SimpleNamespace(budget_amount=0)

        result = admin_save_staff_contract(
            session,
            league_slug="bowl-cap",
            season_start_year=1968,
            team_id=1,
            staff_fhm_id="77",
            role="scout",
            annual_salary=50_000,
            contract_years=1,
            fhm_team_id="24",
        )

        self.assertTrue(result.ok)
        self.assertIsNotNone(ghost.fired_at)
        session.add.assert_called_once()


if __name__ == "__main__":
    unittest.main()
