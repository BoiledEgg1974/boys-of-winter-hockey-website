"""Expansion draft eligibility for UFA-flagged players still tied to a BOWL org."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.expansion_draft_state import (
    player_excluded_from_expansion_pool,
    player_is_unrestricted_free_agent,
    replace_eligible_players,
)


class ExpansionUfaEligibilityTest(unittest.TestCase):
    def test_ufa_with_org_is_not_excluded(self) -> None:
        pl = SimpleNamespace(
            id=101,
            contract=SimpleNamespace(is_ufa=True),
        )
        session = MagicMock()
        org = SimpleNamespace(id=7, name="Anaheim Mighty Ducks")
        with patch(
            "app.services.expansion_draft_state.organization_main_team",
            return_value=org,
        ):
            self.assertTrue(player_is_unrestricted_free_agent(pl))
            self.assertFalse(player_excluded_from_expansion_pool(session, pl))

    def test_ufa_without_org_is_excluded(self) -> None:
        pl = SimpleNamespace(
            id=102,
            contract=SimpleNamespace(is_ufa=True),
        )
        session = MagicMock()
        with patch(
            "app.services.expansion_draft_state.organization_main_team",
            return_value=None,
        ):
            self.assertTrue(player_excluded_from_expansion_pool(session, pl))

    def test_non_ufa_is_not_excluded(self) -> None:
        pl = SimpleNamespace(
            id=103,
            contract=SimpleNamespace(is_ufa=False),
        )
        session = MagicMock()
        self.assertFalse(player_excluded_from_expansion_pool(session, pl))

    def test_replace_eligible_players_keeps_ufa_with_org(self) -> None:
        draft = SimpleNamespace(id=1)
        session = MagicMock()
        ufa_org = SimpleNamespace(id=201, contract=SimpleNamespace(is_ufa=True))
        ufa_free = SimpleNamespace(id=202, contract=SimpleNamespace(is_ufa=True))

        def get_player(_session, pid):
            return {201: ufa_org, 202: ufa_free}.get(int(pid))

        session.get.side_effect = get_player
        session.scalars.return_value.all.return_value = [ufa_org, ufa_free]

        def org_for(_session, pl, **_kwargs):
            return SimpleNamespace(id=7) if int(pl.id) == 201 else None

        with patch(
            "app.services.expansion_draft_state.organization_main_team",
            side_effect=org_for,
        ):
            replace_eligible_players(session, draft, {201, 202})

        saved_ids = {
            call.args[0].player_id
            for call in session.add.call_args_list
            if hasattr(call.args[0], "player_id")
        }
        self.assertEqual(saved_ids, {201})


if __name__ == "__main__":
    unittest.main()
