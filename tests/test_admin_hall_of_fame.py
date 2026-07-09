"""Hall of Fame admin form and service behavior."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.models import Player
from app.services.admin_hall_of_fame import (
    normalize_hof_member_kind,
    normalize_hof_player_query,
    resolve_player_for_hof,
)


class HallOfFameAdminTest(unittest.TestCase):
    def test_admin_form_can_choose_skater_or_goalie(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_hall_of_fame.html"
        text = path.read_text(encoding="utf-8")

        self.assertIn('name="member_kind"', text)
        self.assertIn('value="skater"', text)
        self.assertIn('value="goalie"', text)
        self.assertIn("edit_row.member_kind", text)
        self.assertIn("hof-player-autocomplete", text)
        self.assertIn("api.search_players", text)
        self.assertIn("get_flashed_messages", text)
        self.assertIn('name="player_id"', text)
        self.assertNotIn("hof-player-names", text)

    def test_admin_route_saves_selected_member_kind(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "site_portal.py"
        text = path.read_text(encoding="utf-8")

        self.assertIn('request.form.get("member_kind")', text)
        self.assertIn("member_kind=member_kind", text)
        self.assertIn('request.form.get("player_id")', text)
        self.assertNotIn("player_name_choices", text)

    def test_normalize_hof_member_kind_accepts_only_supported_categories(self) -> None:
        self.assertEqual(normalize_hof_member_kind("Skater"), "skater")
        self.assertEqual(normalize_hof_member_kind(" goalie "), "goalie")
        self.assertIsNone(normalize_hof_member_kind("coach"))

    def test_normalize_hof_player_query_strips_trailing_site_id(self) -> None:
        self.assertEqual(normalize_hof_player_query("Glenn Resch #10952"), ("Glenn Resch", 10952))
        self.assertEqual(normalize_hof_player_query("10952"), ("10952", 10952))
        self.assertEqual(normalize_hof_player_query("  Glenn Resch  "), ("Glenn Resch", None))

    def test_resolve_player_for_hof_uses_explicit_player_id(self) -> None:
        player = SimpleNamespace(id=10952, full_name="Glenn Resch")
        session = MagicMock()
        session.get.return_value = player

        result = resolve_player_for_hof(session, "", player_id=10952)

        self.assertIsNone(result.error)
        self.assertEqual(result.player, player)
        session.get.assert_called_once_with(Player, 10952)

    @unittest.skipUnless(
        Path(__file__).resolve().parents[1].joinpath("instance", "league3.db").is_file(),
        "instance/league3.db required",
    )
    def test_resolve_glenn_resch_in_bowl_cap_db(self) -> None:
        import sqlite3

        db_path = Path(__file__).resolve().parents[1] / "instance" / "league3.db"
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT id, full_name FROM players WHERE full_name = 'Glenn Resch'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        player_id, full_name = row

        session = MagicMock()
        player_obj = SimpleNamespace(id=player_id, full_name=full_name)
        session.get.return_value = player_obj

        def scalars_side_effect(_query):
            result = MagicMock()
            result.all.return_value = [player_obj]
            return result

        session.scalars.side_effect = scalars_side_effect

        by_id = resolve_player_for_hof(session, "", player_id=int(player_id))
        self.assertIsNone(by_id.error)

        by_name = resolve_player_for_hof(session, full_name)
        self.assertIsNone(by_name.error)
        self.assertEqual(by_name.player.full_name, full_name)


if __name__ == "__main__":
    unittest.main()
