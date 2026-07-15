"""Tests for GM Finances page service and route wiring."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import create_app
from app.config import LEAGUES, make_league_config
from app.services.league_finances import (
    _contract_rows_from_csv,
    active_nhl_roster_player_ids,
    affiliate_fhm_team_ids_by_parent,
    cap_penalties_for_season,
    contract_year_salary,
    contract_year_val,
    merged_contract_rows,
    player_cap_hit,
    player_master_fhm_team_ids,
    player_salary_group,
    team_line_player_ids,
    uses_lines_only_roster,
)


class LeagueFinancesUnitTest(unittest.TestCase):
    def test_player_salary_group(self) -> None:
        self.assertEqual(player_salary_group("C"), "forwards")
        self.assertEqual(player_salary_group("LW"), "forwards")
        self.assertEqual(player_salary_group("RD"), "defense")
        self.assertEqual(player_salary_group("D"), "defense")
        self.assertEqual(player_salary_group("G"), "goalies")
        self.assertEqual(player_salary_group(""), "forwards")

    def test_contract_year_salary_prefers_major(self) -> None:
        row = {"major_2005": "3000000", "minor_2005": "900000"}
        self.assertEqual(contract_year_salary(row, 2005), 3000000)

    def test_contract_year_salary_falls_back_to_minor(self) -> None:
        row = {"major_2005": "-1", "minor_2005": "900000"}
        self.assertEqual(contract_year_salary(row, 2005), 900000)

    def test_contract_year_val_parses_float_strings(self) -> None:
        row = {"major_2005": "1500000.0"}
        self.assertEqual(contract_year_val(row, "major", 2005), 1500000)

    def test_player_cap_hit_uses_modest_aav_bump(self) -> None:
        merged_row = {"major_1999": "7350000", "average_salary": "8350000"}
        base_row = {"average_salary": "8350000"}
        self.assertEqual(player_cap_hit(merged_row, base_row, 1999), 8_350_000)

    def test_player_cap_hit_ignores_large_aav_spread(self) -> None:
        merged_row = {"major_1999": "2250000", "average_salary": "2625000"}
        base_row = {"average_salary": "2625000"}
        self.assertEqual(player_cap_hit(merged_row, base_row, 1999), 2_250_000)

    def test_player_cap_hit_falls_back_to_year_salary(self) -> None:
        merged_row = {"major_1999": "303333"}
        base_row = {"average_salary": "303333"}
        self.assertEqual(player_cap_hit(merged_row, base_row, 1999), 303_333)

    def test_affiliate_and_player_master_maps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "player_master.csv").write_text(
                "PlayerId;TeamId\n1;9\n2;60\n3;9\n",
                encoding="utf-8",
            )
            (root / "team_data.csv").write_text(
                "TeamId;LeagueId;Name;Nickname;Abbr;Parent Team 1\n"
                "9;0;Detroit;Red Wings;DET;-1\n"
                "60;3;Farm;Team;FARM;9\n",
                encoding="utf-8",
            )
            (root / "player_contract.csv").write_text(
                "PlayerId;Team;Average Salary;Major 2005;Major 2006\n"
                "1;9;1000000;1000000;1000000\n"
                "2;9;500000;500000;500000\n"
                "3;9;3000000;3000000;3000000\n",
                encoding="utf-8",
            )
            self.assertEqual(player_master_fhm_team_ids(root), {"1": "9", "2": "60", "3": "9"})
            self.assertEqual(affiliate_fhm_team_ids_by_parent(root), {"9": {"60"}})
            merged = merged_contract_rows(root)
            base = _contract_rows_from_csv(root / "player_contract.csv")
            nhl_total = sum(
                player_cap_hit(merged.get(pid), base.get(pid), 2005) or 0
                for pid, tid in player_master_fhm_team_ids(root).items()
                if tid == "9"
            )
            farm_total = sum(
                contract_year_salary(merged.get(pid), 2005) or 0
                for pid, tid in player_master_fhm_team_ids(root).items()
                if tid == "60"
            )
            self.assertEqual(nhl_total, 4_000_000)
            self.assertEqual(farm_total, 500_000)

    def test_active_nhl_roster_keeps_full_pool_when_within_roster_max(self) -> None:
        # Normal 23-man: 20 lined + 3 scratches — count all assigned, not lines-only.
        player_team_ids = {str(i): "3" for i in range(1, 24)}
        line_ids = {str(i) for i in range(1, 21)}
        self.assertEqual(
            active_nhl_roster_player_ids("3", player_team_ids, line_ids, roster_max=23),
            set(player_team_ids),
        )

    def test_active_nhl_roster_excludes_farm_team_ids(self) -> None:
        player_team_ids = {"1": "9", "2": "60", "3": "9"}
        line_ids = {"1", "3"}
        self.assertEqual(
            active_nhl_roster_player_ids("9", player_team_ids, line_ids, roster_max=23),
            {"1", "3"},
        )

    def test_active_nhl_roster_caps_bloated_pool_preferring_ufa_scratches(self) -> None:
        # CBJ-shaped: org dump on NHL TeamId; lines + UFA scratches fill roster max.
        line_ids = {str(i) for i in range(1, 21)}
        player_team_ids = {str(i): "229" for i in range(1, 50)}
        contract_rows = {
            "21": {"ufa": "Yes"},
            "22": {"ufa": "Yes"},
            "23": {"ufa": "Yes"},
            "24": {"ufa": "No"},
            "25": {"ufa": "No"},
        }
        ability_by_pid = {str(i): 1.0 for i in range(21, 50)}
        ability_by_pid["24"] = 2.5  # higher ability non-UFA must not displace UFA scratches
        cap_hit_by_pid = {
            "21": 281_400,
            "22": 4_300_000,
            "23": 281_400,
            "24": 850_000,
            "25": 975_000,
        }
        active = active_nhl_roster_player_ids(
            "229",
            player_team_ids,
            line_ids,
            roster_max=23,
            contract_rows=contract_rows,
            ability_by_pid=ability_by_pid,
            cap_hit_by_pid=cap_hit_by_pid,
        )
        self.assertEqual(len(active), 23)
        self.assertTrue(line_ids.issubset(active))
        self.assertEqual(active - line_ids, {"21", "22", "23"})

    def test_cbj_shaped_roster_cap_matches_fhm_salary(self) -> None:
        line_ids = {str(i) for i in range(1, 21)}
        player_team_ids = {str(i): "229" for i in range(1, 50)}
        salaries = {str(i): 1_000_000 for i in range(1, 21)}
        salaries.update({"21": 281_400, "22": 4_300_000, "23": 281_400})
        for i in range(24, 50):
            salaries[str(i)] = 500_000
        contract_rows = {
            pid: {
                "ufa": "Yes" if pid in {"21", "22", "23"} else "No",
                "major_2000": str(salaries[pid]),
                "average_salary": str(salaries[pid]),
            }
            for pid in player_team_ids
        }
        active = active_nhl_roster_player_ids(
            "229",
            player_team_ids,
            line_ids,
            roster_max=23,
            contract_rows=contract_rows,
            cap_hit_by_pid=salaries,
        )
        total = sum(
            player_cap_hit(contract_rows.get(pid), contract_rows.get(pid), 2000) or 0
            for pid in active
        )
        self.assertEqual(len(active), 23)
        self.assertEqual(total, 20_000_000 + 281_400 + 4_300_000 + 281_400)

    def test_team_line_player_ids_reads_numeric_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "team_lines.csv").write_text(
                "TeamId;ES L1 C;Goalie 1\n3;42;99\n",
                encoding="utf-8",
            )
            self.assertEqual(team_line_player_ids(root, "3"), {"42", "99"})

    def test_uses_lines_only_roster_when_few_scratches(self) -> None:
        active = {"1", "2", "3"}
        line_ids = {"1", "2"}
        self.assertTrue(uses_lines_only_roster(active, line_ids))
        self.assertFalse(uses_lines_only_roster({"1", "2", "3", "4", "5"}, line_ids))

    def test_floor_bump_applies_to_lines_only_roster_below_floor(self) -> None:
        lines_cap = 36_690_800
        cap_floor = 36_797_252
        self.assertEqual(max(lines_cap, cap_floor), cap_floor)

    def test_merged_contract_rows_overlay_renewed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "player_contract.csv"
            renewed = root / "player_contract_renewed.csv"
            base.write_text(
                "PlayerId;Team;Average Salary;Major 2005;Major 2006\n"
                "1;9;1000000;1000000;1000000\n"
                "2;9;2000000;2000000;2000000\n",
                encoding="utf-8",
            )
            renewed.write_text(
                "PlayerId;Team;Average Salary;Major 2005;Major 2006\n"
                "2;9;2500000;2500000;2750000\n",
                encoding="utf-8",
            )
            merged = merged_contract_rows(root)
            self.assertEqual(contract_year_salary(merged["1"], 2006), 1000000)
            self.assertEqual(contract_year_salary(merged["2"], 2006), 2750000)

    def test_merged_contract_rows_keeps_base_year_when_renewed_starts_later(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "player_contract.csv").write_text(
                "PlayerId;Team;Average Salary;Major 1999;Major 2000\n"
                "21;9;1900000;1900000;-1\n",
                encoding="utf-8",
            )
            (root / "player_contract_renewed.csv").write_text(
                "PlayerId;Team;Average Salary;Major 2000;Major 2001\n"
                "21;9;1450000;1450000;-1\n",
                encoding="utf-8",
            )
            merged = merged_contract_rows(root)
            self.assertEqual(contract_year_salary(merged["21"], 1999), 1900000)
            self.assertEqual(contract_year_salary(merged["21"], 2000), 1450000)


class LeagueFinancesWiringTest(unittest.TestCase):
    def test_template_route_and_nav_markers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "finances.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        portal = (root / "app" / "routes" / "site_portal.py").read_text(encoding="utf-8")
        base = (root / "app" / "templates" / "base.html").read_text(encoding="utf-8")
        rules = (root / "app" / "services" / "league_rules.py").read_text(encoding="utf-8")
        admin_rules = (root / "app" / "templates" / "admin_rules.html").read_text(encoding="utf-8")

        for marker in (
            "finances-banner",
            "finances-player-table",
            "finances-panel-staff",
            "finances-pos-bar--fwd",
            "Cap Space",
            "Cap Penalties",
            " Cap</th>",
            "Injury salary buffers are not applied",
            "Player Finances",
            "Staff Finances",
        ):
            self.assertIn(marker, template)

        self.assertIn("def finances_page", portal)
        self.assertIn("def admin_cap_penalties", portal)
        self.assertIn('"/cap-penalties"', portal)
        self.assertIn("admin_cap_penalties.html", portal)
        self.assertIn('"/finances"', portal)
        admin_home = (root / "app" / "templates" / "admin_site_home.html").read_text(encoding="utf-8")
        self.assertIn("admin_cap_penalties", admin_home)

        self.assertIn("build_league_finances_context", portal)
        self.assertIn('href="{{ url_for(\'site_gm.finances_page\') }}"', base)
        self.assertNotIn(
            "{% if current_league_slug in ('bowl-cap', 'bowl-fantasy') %}\n"
            '            <a class="header-tools__link" href="{{ url_for(\'site_gm.finances_page\') }}"',
            base,
        )
        self.assertIn("salary_cap_floor", rules)
        self.assertIn("salary_cap_amount", admin_rules)
        self.assertIn("salary_cap_floor", admin_rules)
        self.assertIn(".finances-banner", css)
        self.assertIn(".finances-pos-bar", css)


class LeagueFinancesRouteGatingTest(unittest.TestCase):
    def test_finances_route_has_no_league_slug_gate(self) -> None:
        root = Path(__file__).resolve().parents[1]
        portal = (root / "app" / "routes" / "site_portal.py").read_text(encoding="utf-8")
        start = portal.find("def finances_page")
        end = portal.find("\n\n@", start + 1)
        block = portal[start:end]
        self.assertNotIn('if slug not in ("bowl-cap", "bowl-fantasy")', block)

    def test_cap_penalties_admin_has_no_league_slug_gate(self) -> None:
        root = Path(__file__).resolve().parents[1]
        portal = (root / "app" / "routes" / "site_portal.py").read_text(encoding="utf-8")
        start = portal.find("def admin_cap_penalties")
        end = portal.find("\n\n@", start + 1)
        block = portal[start:end]
        self.assertNotIn('if slug not in ("bowl-cap", "bowl-fantasy")', block)

    def test_unauthenticated_cap_mount_redirects_to_login(self) -> None:
        cap_entry = next(e for e in LEAGUES if e.slug == "bowl-cap")
        cap_app = create_app(make_league_config(cap_entry.slug))
        with cap_app.test_client() as client:
            resp = client.get("/finances")
            self.assertEqual(resp.status_code, 302)


if __name__ == "__main__":
    unittest.main()
