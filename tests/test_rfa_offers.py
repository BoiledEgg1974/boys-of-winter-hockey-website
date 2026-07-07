"""RFA offer sheet rules and workflow helpers."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.rfa_offers import (
    CATEGORY_LABELS,
    MIN_OFFER_MULTIPLIER,
    accept_odds_percent,
    compensation_for_offer,
    compensation_panel_dict,
    compensation_reference_rows,
    derive_rfa_category,
    is_rfa_eligible,
    minimum_offer_amount,
    roll_player_accepts,
    validate_offer_submission,
    _tier_for_pct,
    _unsigned_european_draft_years,
    CompensationPreview,
)
from app.services.player_contract_csv import contract_export_is_ufa


class RfaCategoryTest(unittest.TestCase):
    def test_group_i_under_25_few_pro_seasons(self):
        player = MagicMock(birth_date=date(2002, 1, 1), nationality="CAN", fhm_player_id="1")
        session = MagicMock()
        with patch("app.services.rfa_offers._raw_import_dir", return_value=MagicMock()), patch(
            "app.services.rfa_offers._unsigned_european_draft_years", return_value=None
        ), patch(
            "app.services.rfa_offers._pro_seasons_before", return_value=3
        ):
            cat, expl = derive_rfa_category(session, player, season_start_year=2026, age=24)
        self.assertEqual(cat, "group_i")
        self.assertIn("24", expl)

    def test_group_ii_age_27(self):
        player = MagicMock(birth_date=date(1999, 1, 1), nationality="USA", fhm_player_id="2")
        session = MagicMock()
        with patch("app.services.rfa_offers._raw_import_dir", return_value=MagicMock()), patch(
            "app.services.rfa_offers._unsigned_european_draft_years", return_value=None
        ), patch(
            "app.services.rfa_offers._pro_seasons_before", return_value=8
        ):
            cat, _ = derive_rfa_category(session, player, season_start_year=2026, age=27)
        self.assertEqual(cat, "group_ii")

    def test_group_iv_european_unsigned(self):
        player = MagicMock(birth_date=date(2003, 1, 1), nationality="SWE", fhm_player_id="3", id=3)
        session = MagicMock()
        with patch("app.services.rfa_offers._raw_import_dir", return_value=MagicMock()), patch(
            "app.services.rfa_offers._unsigned_european_draft_years", return_value=3
        ):
            cat, expl = derive_rfa_category(session, player, season_start_year=2026, age=23)
        self.assertEqual(cat, "group_iv")
        self.assertIn("European", expl)


class RfaEligibilityExportSyncTest(unittest.TestCase):
    def _write_contract_csv(self, rows: list[str]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "player_contract.csv"
        path.write_text(
            "PlayerId;Team;NTC;NMC;ELC;UFA;Scholarship;Average Salary;Major 2025;Major 2026\n"
            + "\n".join(rows)
            + "\n",
            encoding="utf-8",
        )
        return Path(tmp.name)

    def test_is_rfa_eligible_false_when_export_row_missing(self) -> None:
        raw = self._write_contract_csv(["999;17;No;No;No;No;-;500000;-1;-1"])
        player = SimpleNamespace(fhm_player_id="14299")
        contract = SimpleNamespace(is_ufa=False)
        self.assertIsNone(contract_export_is_ufa("14299", raw))
        self.assertFalse(
            is_rfa_eligible(
                MagicMock(),
                player,
                contract,
                season_start_year=2026,
                raw_dir=raw,
            )
        )

    def test_is_rfa_eligible_false_when_export_marks_ufa(self) -> None:
        raw = self._write_contract_csv(["14299;17;No;No;No;Yes;-;500000;-1;-1"])
        player = SimpleNamespace(fhm_player_id="14299")
        contract = SimpleNamespace(is_ufa=False)
        self.assertTrue(contract_export_is_ufa("14299", raw))
        self.assertFalse(
            is_rfa_eligible(
                MagicMock(),
                player,
                contract,
                season_start_year=2026,
                raw_dir=raw,
            )
        )

    def test_is_rfa_eligible_true_when_export_rfa_and_expired(self) -> None:
        raw = self._write_contract_csv(["14299;17;No;No;No;No;-;500000;-1;-1"])
        player = SimpleNamespace(fhm_player_id="14299")
        contract = SimpleNamespace(is_ufa=False)
        self.assertFalse(contract_export_is_ufa("14299", raw))
        self.assertTrue(
            is_rfa_eligible(
                MagicMock(),
                player,
                contract,
                season_start_year=2026,
                raw_dir=raw,
            )
        )

    def test_group_iv_skipped_when_player_has_pro_seasons(self) -> None:
        player = MagicMock(
            birth_date=date(1977, 6, 12),
            nationality="Germany",
            fhm_player_id="14299",
            id=6534,
        )
        draft_pick = SimpleNamespace(draft_year=1995)
        session = MagicMock()
        session.scalars.return_value.all.return_value = [draft_pick]
        raw = Path("/tmp/unused")
        with patch("app.services.rfa_offers._is_european", return_value=True), patch(
            "app.services.rfa_offers.contract_export_row", return_value=None
        ), patch(
            "app.services.rfa_offers._pro_seasons_before", return_value=6
        ):
            self.assertIsNone(
                _unsigned_european_draft_years(
                    session, player, 2026, raw_dir=raw
                )
            )
        with patch("app.services.rfa_offers._raw_import_dir", return_value=MagicMock()), patch(
            "app.services.rfa_offers._unsigned_european_draft_years", return_value=None
        ), patch(
            "app.services.rfa_offers._pro_seasons_before", return_value=6
        ):
            cat, _ = derive_rfa_category(session, player, season_start_year=2026, age=49)
        self.assertEqual(cat, "group_iii")


class RfaMinimumOfferTest(unittest.TestCase):
    def test_multipliers(self):
        self.assertEqual(minimum_offer_amount("group_i", 1_000_000), 1_100_000)
        self.assertEqual(minimum_offer_amount("group_ii", 1_000_000), 1_200_000)
        self.assertEqual(minimum_offer_amount("group_iii", 1_000_000), 1_000_000)
        self.assertEqual(minimum_offer_amount("group_iv", 1_000_000), 500_000)
        for cat, mult in MIN_OFFER_MULTIPLIER.items():
            self.assertGreater(minimum_offer_amount(cat, 0), 0)


class RfaCompensationTest(unittest.TestCase):
    def test_percent_tier_boundaries(self):
        self.assertEqual(_tier_for_pct(1.6)[0], "none")
        self.assertEqual(_tier_for_pct(1.7)[0], "3rd")
        self.assertEqual(_tier_for_pct(2.6)[0], "2nd")
        self.assertEqual(_tier_for_pct(13.0)[0], "three_1st_two_2nd")

    def test_compensation_reference_rows_at_cap(self):
        rows = compensation_reference_rows(80_000_000)
        self.assertEqual(len(rows), 7)
        self.assertEqual(rows[0]["tier_key"], "none")
        self.assertIsNotNone(rows[1]["lo_dollars"])

    def test_group_ii_compensation_uses_percent_of_cap(self):
        session = MagicMock()
        with (
            patch("app.services.rfa_offers._season_start_year", return_value=2000),
            patch("app.services.rfa_offers.cap_for_season", return_value=(80_000_000, 60_000_000)),
            patch("app.services.rfa_offers.owned_draft_picks_for_team", return_value=[]),
        ):
            comp = compensation_for_offer(
                session,
                session,
                league_slug="bowl-fantasy",
                offering_team_id=1,
                offer_salary=1_360_000,
                category="group_ii",
            )
        self.assertEqual(comp.tier_key, "3rd")
        self.assertAlmostEqual(comp.offer_pct_of_cap or 0, 1.7, places=1)

    def test_group_ii_compensation_panel_applies(self):
        comp = CompensationPreview(
            tier_key="2nd",
            label="2nd Round Selection",
            scaled_min=250_000,
            scaled_max=374_999,
            pick_requirements=[{"round": 2, "count": 1}],
            draft_year=2027,
            picks_available=["2027 2nd (BOS)"],
            picks_missing=[],
            valid=True,
        )
        panel = compensation_panel_dict(comp, category="group_ii")
        self.assertTrue(panel["applies"])
        self.assertTrue(panel["valid"])

    def test_non_group_ii_no_compensation_in_service(self):
        session = MagicMock()
        with patch("app.services.rfa_offers._season_start_year", return_value=2026), patch(
            "app.services.rfa_offers.cap_for_season", return_value=(80_000_000, 60_000_000)
        ), patch("app.services.rfa_offers.owned_draft_picks_for_team", return_value=[]):
            comp = compensation_for_offer(
                session,
                session,
                league_slug="bowl-fantasy",
                offering_team_id=1,
                offer_salary=5_000_000,
                category="group_i",
            )
        self.assertEqual(comp.tier_key, "none")

    def test_validate_group_ii_blocks_missing_cap(self):
        candidate = MagicMock()
        candidate.minimum_offer = 100_000
        candidate.player.id = 10
        candidate.category = "group_ii"
        comp = CompensationPreview(
            tier_key="none",
            label="No Compensation",
            scaled_min=0,
            scaled_max=0,
            pick_requirements=[],
            draft_year=2027,
            picks_available=[],
            picks_missing=[],
            valid=False,
            cap_missing=True,
        )
        with patch("app.services.rfa_offers.list_rfa_candidates", return_value=[candidate]), patch(
            "app.services.rfa_offers.compensation_for_offer", return_value=comp
        ):
            _, _, err = validate_offer_submission(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-historical",
                offering_team_id=2,
                player_id=10,
                offer_salary=500_000,
                offer_years=2,
            )
        self.assertIn("cap ceiling", err or "")


class RfaOddsTest(unittest.TestCase):
    def test_accept_odds_by_category(self):
        self.assertEqual(accept_odds_percent("group_i", "angry"), 75)
        self.assertEqual(accept_odds_percent("group_iii", "angry"), 45)
        self.assertEqual(accept_odds_percent("group_iii", "super_happy"), 100)

    def test_roll_player_accepts_is_bool(self):
        with patch("app.services.rfa_offers.random.random", return_value=0.5):
            accepted, roll = roll_player_accepts("group_i", "okay")
        self.assertIsInstance(accepted, bool)
        self.assertEqual(roll, 50.0)


class RfaValidationTest(unittest.TestCase):
    def test_validate_rejects_below_minimum(self):
        candidate = MagicMock()
        candidate.minimum_offer = 500_000
        candidate.player.id = 10
        candidate.category = "group_ii"
        with patch("app.services.rfa_offers.list_rfa_candidates", return_value=[candidate]):
            _, _, err = validate_offer_submission(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-fantasy",
                offering_team_id=2,
                player_id=10,
                offer_salary=100_000,
                offer_years=1,
            )
        self.assertIn("at least", err or "")

    def test_validate_group_ii_requires_picks(self):
        candidate = MagicMock()
        candidate.minimum_offer = 100_000
        candidate.player.id = 10
        candidate.category = "group_ii"
        comp = CompensationPreview(
            tier_key="1st",
            label="1st",
            scaled_min=0,
            scaled_max=999,
            pick_requirements=[{"round": 1, "count": 1}],
            draft_year=2027,
            picks_available=[],
            picks_missing=["1× 2027 1st round"],
            valid=False,
        )
        with patch("app.services.rfa_offers.list_rfa_candidates", return_value=[candidate]), patch(
            "app.services.rfa_offers.compensation_for_offer", return_value=comp
        ):
            _, _, err = validate_offer_submission(
                MagicMock(),
                MagicMock(),
                league_slug="bowl-fantasy",
                offering_team_id=2,
                player_id=10,
                offer_salary=500_000,
                offer_years=2,
            )
        self.assertIn("draft picks", err or "")


class RfaTemplateRouteTest(unittest.TestCase):
    def test_gm_template_has_player_links_and_modal(self):
        from pathlib import Path

        text = Path("app/templates/rfa_offers.html").read_text(encoding="utf-8")
        self.assertIn("main.player_page", text)
        self.assertIn("rfa-offer-modal", text)
        self.assertIn("rfa_compensation_preview", text)
        self.assertIn("submit_disabled", text)
        self.assertIn("rfa-comp-reference", text)
        self.assertIn("cap_panels_view", text)
        self.assertIn("Own RFA", text)

    def test_admin_template_has_cap_panels_and_season_labels(self):
        from pathlib import Path

        text = Path("app/templates/admin_rfa_offers.html").read_text(encoding="utf-8")
        self.assertIn("Salary cap schedule", text)
        self.assertIn("season_label", text)
        self.assertIn("save_cap_panel", text)

    def test_gm_page_lists_all_candidates_not_filtered_by_own_team(self):
        from pathlib import Path

        text = Path("app/routes/site_portal.py").read_text(encoding="utf-8")
        self.assertIn("candidates = list_rfa_candidates(db.session, league_slug=slug)", text)

    def test_admin_template_has_happiness_and_decision(self):
        from pathlib import Path

        text = Path("app/templates/admin_rfa_offer_detail.html").read_text(encoding="utf-8")
        self.assertIn("admin_rfa_set_happiness", text)
        self.assertIn("admin_rfa_player_decision", text)
        self.assertIn("Player's Decision", text)

    def test_category_labels_complete(self):
        for key in ("group_i", "group_ii", "group_iii", "group_iv"):
            self.assertIn(key, CATEGORY_LABELS)


class RfaNotificationKindsTest(unittest.TestCase):
    def test_admin_notify_function_exists(self):
        from app.services.admin_review_notify import notify_rfa_offer_pending

        self.assertTrue(callable(notify_rfa_offer_pending))

    def test_gm_notification_kinds_registered_in_portal(self):
        from pathlib import Path

        text = Path("app/routes/site_portal.py").read_text(encoding="utf-8")
        for kind in ("admin_review_rfa", "rfa_awaiting_match", "rfa_player_rejected"):
            self.assertIn(kind, text)


if __name__ == "__main__":
    unittest.main()
