"""Player analytics display helpers."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services import player_analytics as pa


class PlayerAnalyticsTests(unittest.TestCase):
    def test_linked_assignment_uses_actual_defense_position(self) -> None:
        self.assertEqual(
            pa._linked_assignment_card_text("es_l1_lw", {"position": "D"}),
            "D - 1st line",
        )

    def test_linked_assignment_keeps_actual_forward_position(self) -> None:
        self.assertEqual(
            pa._linked_assignment_card_text("pp5on4_l2_ld", {"position": "RW"}),
            "RW - 2nd Unit",
        )


class AssignmentParsingTests(unittest.TestCase):
    def test_assignment_label_es_line(self) -> None:
        self.assertEqual(pa._assignment_label("es_l1_c"), "ES L1 C")

    def test_assignment_label_pp_unit(self) -> None:
        self.assertEqual(pa._assignment_label("pp5on4_l2_rw"), "PP L2 RW")

    def test_assignment_label_goalie_slot(self) -> None:
        self.assertEqual(pa._assignment_label("goalie_1"), "Goalie 1")

    def test_find_assignments_and_linemates(self) -> None:
        team_lines = {
            3: {
                "assignments": {
                    "es_l1_c": "100",
                    "es_l1_lw": "101",
                    "es_l1_rw": "102",
                    "pp5on4_l1_f1": "100",
                    "pp5on4_l1_f2": "103",
                    "goalie_1": "200",
                },
            }
        }
        found = pa._find_player_assignments(team_lines, 3, "100")
        labels = {a["label"] for a in found}
        self.assertIn("ES L1 C", labels)
        groups = pa._deployment_groups(found)
        self.assertEqual(groups[0]["assignments"][0]["assignment"], "C - 1st line")
        self.assertEqual(groups[1]["assignments"][0]["assignment"], "F1 - 1st Unit")
        mates = pa._linemates_from_assignments(team_lines, 3, "100", session=None)
        mate_ids = {m["fhm_player_id"] for m in mates}
        self.assertEqual(mate_ids, {"101", "102", "103"})
        mate_groups = pa._linemate_groups(mates)
        self.assertEqual(mate_groups[0]["title"], "Even Strength Linemates")
        self.assertEqual(mate_groups[1]["title"], "Powerplay Linemates")
        self.assertEqual(pa._team_line_team_for_player(team_lines, "100", None), 3)
        self.assertEqual(pa._team_line_team_for_player(team_lines, "100", 99), 3)


class RoleTierTests(unittest.TestCase):
    def test_skater_top_line_tier_with_usage(self) -> None:
        idx = pa._skater_role_index(
            pos_rating=18.0,
            abi=4.0,
            pot=4.5,
            ovr=82,
            toi_pg_sec=21 * 60,
            ppg=1.1,
            assignments=[{"label": "ES L1 C"}],
        )
        self.assertGreaterEqual(idx, 3)
        self.assertEqual(pa.SKATER_ROLE_TIERS[idx], pa.SKATER_ROLE_TIERS[min(idx, 5)])

    def test_goalie_not_qualified_without_usage(self) -> None:
        idx = pa._goalie_role_index(
            pos_rating=14.0,
            abi=2.0,
            pot=2.5,
            gp=0,
            gs=None,
            minutes=None,
            assignments=[],
            sv_pct=None,
            gr=None,
        )
        self.assertEqual(pa.GOALIE_ROLE_TIERS[idx], "Not Qualified")

    def test_goalie_starter_tier(self) -> None:
        idx = pa._goalie_role_index(
            pos_rating=17.0,
            abi=4.2,
            pot=4.0,
            gp=40,
            gs=32,
            minutes=2200,
            assignments=[{"label": "Goalie 1"}],
            sv_pct=0.918,
            gr=72.0,
        )
        self.assertGreaterEqual(idx, 3)


class BuildPanelTests(unittest.TestCase):
    def test_retired_player_disabled(self) -> None:
        player = SimpleNamespace(
            id=1,
            fhm_player_id="1",
            retired=True,
            current_team=None,
            position="C",
        )
        session = MagicMock()
        out = pa.build_player_analytics_panel(
            session,
            player,
            ratings_row=None,
            season=None,
            is_goalie=False,
            use_goalie_game_log=False,
            game_log=[],
            position_ratings_rows=[],
            hero_abi=None,
            hero_pot=None,
            player_ovr=None,
            season_trend_rows=[],
            goalie_trend_mode=False,
            retired=True,
        )
        self.assertFalse(out["enabled"])
        self.assertIn("Retired", out["summary_meta"])

    def test_load_team_lines_from_csv(self) -> None:
        csv_body = (
            "TeamId;ES L1 C;ES L1 LW;PP5on4 L1 RW;PK4on5 L2 F2;Goalie 1\n"
            "42;5001;5002;5003;5004;9001\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            raw = Path(tmp)
            (raw / "team_lines.csv").write_text(csv_body, encoding="utf-8")
            by_team = pa._load_team_lines(raw)
            self.assertIn(42, by_team)
            assigns = by_team[42]["assignments"]
            self.assertEqual(assigns.get("es_l1_c"), "5001")
            self.assertEqual(assigns.get("pp5on4_l1_rw"), "5003")
            self.assertEqual(assigns.get("pk4on5_l2_f2"), "5004")
            self.assertEqual(assigns.get("goalie_1"), "9001")


class MentalScoreTests(unittest.TestCase):
    def test_teammate_score_maps_20_scale(self) -> None:
        rr = {
            "determination": "18",
            "teamplayer": "17",
            "character": "16",
            "leadership": "15",
            "professionalism": "14",
        }
        score = pa._mental_teammate_score(rr, pa._MENTAL_TEAMMATE_KEYS)
        self.assertIsNotNone(score)
        assert score is not None
        self.assertGreaterEqual(score, 40)
        self.assertLessEqual(score, 100)


if __name__ == "__main__":
    unittest.main()
