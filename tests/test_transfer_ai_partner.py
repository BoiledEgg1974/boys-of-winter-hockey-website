"""Transfer AI partner anti-fleece and rule-block tests."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.transfer_ai_partner import evaluate_transfer_proposal
from app.services.transfer_rules import (
    STATUS_AI_DECLINED,
    STATUS_AI_COUNTER,
    STATUS_PENDING_COMMISSIONER,
    _pta_fee_for_player,
    load_transfer_rules_config,
    scale_usd_to_current_cap,
)
from app.services.transfer_tool import validate_transfer_submission
from app.services.transfer_valuation import compensation_offer_value_usd, player_asset_value


class _FakePlayer:
    def __init__(
        self,
        *,
        pid: int = 1,
        name: str = "Test Player",
        ability: float = 70.0,
        potential: float = 75.0,
        birth_date: date | None = date(1998, 5, 1),
        nationality: str = "Sweden",
        team_id: int = 10,
        fhm_player_id: str = "100",
    ):
        self.id = pid
        self.full_name = name
        self.overall_ability = ability
        self.overall_potential = potential
        self.birth_date = birth_date
        self.nationality = nationality
        self.current_team_id = team_id
        self.fhm_player_id = fhm_player_id
        self.retired = False
        self.position = "C"
        self.contract = None


class _FakeTeam:
    def __init__(self, *, tid: int = 10, league_id: int = 5, name: str = "Farjestad"):
        self.id = tid
        self.fhm_league_id = league_id
        self.name = name
        self.nickname = ""
        self.abbreviation = "FHC"

    def full_display_name(self) -> str:
        return self.name


class TransferAiPartnerTests(unittest.TestCase):
    def test_player_asset_value_young_high_pot(self):
        session = MagicMock()
        session.scalar.return_value = 0
        pl = _FakePlayer(ability=65, potential=82, birth_date=date(2004, 1, 1))
        with patch("app.services.transfer_valuation.get_player_ratings_row", return_value=None):
            val = player_asset_value(session, pl, age=20)
        self.assertGreater(val, 60)

    def test_khl_under_contract_blocked(self):
        session = MagicMock()

        def _get(model, pk):
            if pk == 10:
                return _FakeTeam(tid=10, league_id=6)
            if pk == 1:
                return _FakePlayer(team_id=10, nationality="Russia")
            return None

        session.get.side_effect = _get
        with patch(
            "app.services.transfer_ai_partner.build_player_transfer_context"
        ) as mock_ctx:
            ctx = MagicMock()
            ctx.blocked = True
            ctx.block_reason = "KHL blocked"
            ctx.pta_fee_usd = 0
            ctx.player = _FakePlayer()
            mock_ctx.return_value = ctx
            result = evaluate_transfer_proposal(
                session,
                player_ids=[1],
                external_team_id=10,
                bowl_team_id=5,
                compensation={"pta_transfer_fee": 0, "cash_sweetener": 0},
                league_slug="bowl-fantasy",
            )
        self.assertEqual(result.proposal_status, STATUS_AI_DECLINED)
        self.assertIn("KHL", result.ai_rationale)

    def test_fair_offer_accepted(self):
        session = MagicMock()
        ext_team = _FakeTeam(tid=10, league_id=5)
        player = _FakePlayer(team_id=10)
        session.get.side_effect = lambda model, pk: {10: ext_team, 1: player}.get(pk)

        with patch("app.services.transfer_ai_partner.build_player_transfer_context") as mock_ctx:
            ctx = MagicMock()
            ctx.blocked = False
            ctx.pta_fee_usd = 350000
            ctx.player = player
            ctx.age = 24
            ctx.contract_years_remaining = 1
            ctx.average_salary = 800000
            mock_ctx.return_value = ctx
            with patch(
                "app.services.transfer_ai_partner.compensation_offer_value_usd",
                return_value=(2_000_000, {"total_usd": 2_000_000}),
            ):
                with patch(
                    "app.services.transfer_ai_partner.external_club_minimum_ask_usd",
                    return_value=(1_800_000, {"minimum_ask_usd": 1_800_000}),
                ):
                    result = evaluate_transfer_proposal(
                        session,
                        player_ids=[1],
                        external_team_id=10,
                        bowl_team_id=5,
                        compensation={"pta_transfer_fee": 350000, "cash_sweetener": 1_650_000},
                        league_slug="bowl-fantasy",
                    )
        self.assertEqual(result.proposal_status, STATUS_PENDING_COMMISSIONER)
        self.assertEqual(result.ai_verdict, "accept")

    def test_low_offer_counter_not_fleece_accept(self):
        session = MagicMock()
        ext_team = _FakeTeam(tid=10, league_id=5)
        player = _FakePlayer(team_id=10, ability=82, potential=84)
        session.get.side_effect = lambda model, pk: {10: ext_team, 1: player}.get(pk)

        with patch("app.services.transfer_ai_partner.build_player_transfer_context") as mock_ctx:
            ctx = MagicMock()
            ctx.blocked = False
            ctx.pta_fee_usd = 350000
            ctx.player = player
            ctx.age = 26
            ctx.contract_years_remaining = 2
            ctx.average_salary = 1_200_000
            mock_ctx.return_value = ctx
            with patch(
                "app.services.transfer_ai_partner.compensation_offer_value_usd",
                return_value=(900_000, {"total_usd": 900_000}),
            ):
                with patch(
                    "app.services.transfer_ai_partner.external_club_minimum_ask_usd",
                    return_value=(1_500_000, {"minimum_ask_usd": 1_500_000}),
                ):
                    result = evaluate_transfer_proposal(
                        session,
                        player_ids=[1],
                        external_team_id=10,
                        bowl_team_id=5,
                        compensation={"pta_transfer_fee": 350000, "cash_sweetener": 550_000},
                        league_slug="bowl-fantasy",
                    )
        self.assertIn(result.proposal_status, (STATUS_AI_COUNTER, STATUS_AI_DECLINED))
        self.assertNotEqual(result.ai_verdict, "accept")

    def test_compensation_includes_pta(self):
        session = MagicMock()
        session.get.return_value = None
        total, breakdown = compensation_offer_value_usd(
            session,
            compensation={"pta_transfer_fee": 350000, "cash_sweetener": 100000},
            bowl_team_id=5,
            league_slug="bowl-fantasy",
        )
        self.assertEqual(total, 450000)
        self.assertEqual(breakdown["pta_transfer_fee"], 350000)

    def test_house_rules_no_picks_six_leagues(self):
        cfg = load_transfer_rules_config("bowl-fantasy")
        self.assertFalse(cfg.allow_draft_pick_sweeteners)
        self.assertEqual(cfg.max_sweetener_picks, 0)
        self.assertTrue(cfg.allow_player_sweeteners)
        self.assertTrue(cfg.allow_cash_sweetener)
        self.assertEqual(cfg.reference_salary_cap_usd, 95_500_000)
        self.assertEqual(cfg.eligible_external_league_fhm_ids, (5, 6, 7, 8, 9, 16))

    def test_pta_matches_confirmed_table_at_reference_cap(self):
        cfg = load_transfer_rules_config("bowl-fantasy")
        session = MagicMock()
        self.assertEqual(
            _pta_fee_for_player(cfg, league_fhm_id=5, age=24, session=session),
            350000,
        )
        self.assertEqual(
            _pta_fee_for_player(cfg, league_fhm_id=5, age=20, session=session),
            250000,
        )
        self.assertEqual(
            _pta_fee_for_player(cfg, league_fhm_id=5, age=28, session=session),
            450000,
        )

    def test_pta_scales_when_cap_doubles(self):
        self.assertEqual(scale_usd_to_current_cap(350000, None), 350000)
        with patch(
            "app.services.transfer_rules.resolve_transfer_salary_cap_usd",
            return_value=191_000_000,
        ):
            self.assertEqual(scale_usd_to_current_cap(350000, MagicMock()), 700000)

    def test_draft_picks_rejected(self):
        session = MagicMock()
        err = validate_transfer_submission(
            session,
            session,
            league_slug="bowl-fantasy",
            bowl_team_id=1,
            external_team_id=2,
            external_league_fhm_id=5,
            player_ids=[1],
            compensation={"pta_transfer_fee": 350000, "draft_picks": ["dpick:1"]},
            raw_dir=None,
        )
        self.assertIsNotNone(err)
        self.assertIn("Draft picks", err or "")


if __name__ == "__main__":
    unittest.main()
