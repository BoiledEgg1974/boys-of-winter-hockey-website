"""Transfer AI partner anti-fleece and rule-block tests."""
from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

from app.services.transfer_ai_partner import evaluate_transfer_proposal
from app.services.transfer_rules import STATUS_AI_DECLINED, STATUS_AI_COUNTER, STATUS_PENDING_COMMISSIONER
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


if __name__ == "__main__":
    unittest.main()
