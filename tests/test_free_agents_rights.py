"""BOWL org-rights resolution from raw exports."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services.free_agents import (
    bowl_org_rights_player_ids,
    bowl_org_rights_player_ids_for_league,
    bowl_rights_player_ids_from_raw_exports_for_league,
    free_agent_status_key,
)


class FreeAgentsRightsTest(unittest.TestCase):
    def test_league_scoped_raw_rights_uses_league_import_dir(self) -> None:
        session = MagicMock()
        with patch(
            "app.services.free_agents._bowl_rights_player_ids_from_raw_dir",
            return_value={5259},
        ) as read_dir:
            out = bowl_rights_player_ids_from_raw_exports_for_league(
                session, "bowl-fantasy"
            )
        self.assertEqual(out, frozenset({5259}))
        raw_dir = read_dir.call_args[0][1]
        self.assertTrue(str(raw_dir).replace("\\", "/").endswith("raw/bowl_fantasy"))

    def test_org_rights_for_league_combines_db_and_league_raw_exports(self) -> None:
        session = MagicMock()
        with (
            patch(
                "app.services.free_agents.bowl_nhl_org_rights_player_ids",
                return_value=frozenset({1}),
            ),
            patch(
                "app.services.free_agents.bowl_rights_player_ids_from_raw_exports_for_league",
                return_value=frozenset({2}),
            ) as raw_for_league,
        ):
            out = bowl_org_rights_player_ids_for_league(session, "bowl-cap")
        raw_for_league.assert_called_once_with(session, "bowl-cap")
        self.assertEqual(out, frozenset({1, 2}))

    def test_free_agent_status_groups_rfa_and_ufa(self) -> None:
        rfa = SimpleNamespace(contract=SimpleNamespace(is_ufa=False))
        ufa = SimpleNamespace(contract=SimpleNamespace(is_ufa=True))
        uncontracted = SimpleNamespace(contract=None)

        self.assertEqual(free_agent_status_key(rfa), "rfa")
        self.assertEqual(free_agent_status_key(ufa), "ufa")
        self.assertEqual(free_agent_status_key(uncontracted), "ufa")

    def test_free_agents_template_has_rfa_ufa_groups(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "app" / "templates" / "free_agents.html").read_text(encoding="utf-8")
        route_text = (root / "app" / "routes" / "main.py").read_text(encoding="utf-8")
        self.assertIn("fa_groups", text)
        self.assertIn("free-agents-page__group", text)
        self.assertIn("RFA (Restricted Free Agents)", route_text)
        self.assertIn("UFA (Unrestricted Free Agents)", route_text)

    def test_free_agents_route_assigns_players_to_groups(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_client() as client:
            resp = client.get("/free-agents")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"RFA (Restricted Free Agents)", resp.data)
        self.assertIn(b"UFA (Unrestricted Free Agents)", resp.data)
        self.assertLess(
            resp.data.index(b"UFA (Unrestricted Free Agents)"),
            resp.data.index(b"RFA (Restricted Free Agents)"),
        )
        self.assertIn(b"prospects-rankings-table--free-agents", resp.data)
        self.assertNotIn(b"No UFA (Unrestricted Free Agents) match this filter.", resp.data)


if __name__ == "__main__":
    unittest.main()
