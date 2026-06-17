"""Tests for league team PK vs FHM franchise id registry."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.league_team_registry import (
    audit_league_team_ids,
    load_fhm_team_master,
    pk_fhm_collision_rows,
    write_league_team_map_json,
)


class LeagueTeamRegistryTests(unittest.TestCase):
    def test_load_fhm_team_master_cap_has_dallas_and_buffalo(self) -> None:
        rows = load_fhm_team_master("bowl-cap")
        by_abbr = {r.abbreviation: r for r in rows}
        self.assertEqual(by_abbr["DAL"].fhm_team_id, "12")
        self.assertEqual(by_abbr["BUF"].fhm_team_id, "17")

    def test_pk_fhm_collision_detects_buffalo_dallas_crossover(self) -> None:
        teams = [
            {"id": 8, "abbreviation": "DAL", "name": "Dallas", "fhm_team_id": "12"},
            {"id": 12, "abbreviation": "BUF", "name": "Buffalo", "fhm_team_id": "17"},
        ]
        collisions = pk_fhm_collision_rows(teams)
        buf = [c for c in collisions if c["team_pk"] == 12]
        self.assertEqual(len(buf), 1)
        self.assertEqual(buf[0]["other_abbrev"], "DAL")

    def test_audit_cap_local_db_aligns_with_master(self) -> None:
        report = audit_league_team_ids("bowl-cap")
        if report["team_count"] == 0:
            self.skipTest("instance/league3.db not present")
        self.assertEqual(report["team_count"], 28)
        self.assertEqual(report["master_count"], 28)
        self.assertFalse(report["missing_in_db"])
        self.assertFalse(report["fhm_mismatches"])
        self.assertTrue(report["ok"])
        buf = next(t for t in report["teams"] if t["abbreviation"] == "BUF")
        dal = next(t for t in report["teams"] if t["abbreviation"] == "DAL")
        self.assertEqual(buf["team_pk"], 12)
        self.assertEqual(buf["fhm_team_id"], "17")
        self.assertEqual(dal["team_pk"], 8)
        self.assertEqual(dal["fhm_team_id"], "12")

    def test_audit_historical_local_db_aligns_with_master(self) -> None:
        report = audit_league_team_ids("bowl-historical")
        if report["team_count"] == 0:
            self.skipTest("instance/league2.db not present")
        self.assertEqual(report["team_count"], 12)
        self.assertEqual(report["master_count"], 12)
        self.assertFalse(report["missing_in_db"])
        self.assertFalse(report["fhm_mismatches"])
        self.assertTrue(report["ok"])
        lak = next(t for t in report["teams"] if t["abbreviation"] == "LAK")
        self.assertEqual(lak["fhm_team_id"], "118")

    def test_audit_fantasy_local_db_aligns_with_master(self) -> None:
        report = audit_league_team_ids("bowl-fantasy")
        if report["team_count"] == 0:
            self.skipTest("instance/bow.db not present")
        self.assertEqual(report["team_count"], 24)
        self.assertEqual(report["master_count"], 24)
        self.assertFalse(report["missing_in_db"])
        self.assertFalse(report["fhm_mismatches"])
        self.assertTrue(report["ok"])
        wic = next(t for t in report["teams"] if t["abbreviation"] == "WIC")
        self.assertEqual(wic["team_pk"], 1)
        self.assertEqual(wic["fhm_team_id"], "0")

    def test_load_fhm_team_master_fantasy_reads_cp1252_export(self) -> None:
        rows = load_fhm_team_master("bowl-fantasy")
        self.assertGreaterEqual(len(rows), 24)
        by_abbr = {r.abbreviation: r for r in rows}
        self.assertIn("TRL", by_abbr)

    def test_write_map_json_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "map.json"
            with mock.patch(
                "app.services.league_team_registry.audit_league_team_ids",
                return_value={
                    "teams": [
                        {
                            "team_pk": 12,
                            "abbreviation": "BUF",
                            "name": "Buffalo",
                            "fhm_team_id": "17",
                            "slug": "buf-t17",
                            "master_display_name": "Buffalo Sabres",
                        }
                    ],
                    "pk_fhm_collision_warnings": [],
                },
            ):
                path = write_league_team_map_json("bowl-cap", path=out)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["league_slug"], "bowl-cap")
            self.assertEqual(data["teams"][0]["fhm_team_id"], "17")


if __name__ == "__main__":
    unittest.main()
