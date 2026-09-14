"""Team-page statistics table sort markup."""
from __future__ import annotations

import unittest
from pathlib import Path


class StatisticsPanelTemplateTest(unittest.TestCase):
    def test_team_page_stats_sort_in_place(self) -> None:
        root = Path(__file__).resolve().parents[1]
        panel = (root / "app" / "templates" / "_statistics_panel.html").read_text(encoding="utf-8")
        team = (root / "app" / "templates" / "team.html").read_text(encoding="utf-8")
        main = (root / "app" / "routes" / "main.py").read_text(encoding="utf-8")
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn('id="team-page-stats"', team)
        self.assertIn("{% with team_statistics=team %}", team)
        self.assertIn('tmpl_kwargs["team_statistics"] = team', main)
        self.assertIn("data-sortable", panel)
        self.assertIn("data-sort-renumber", panel)
        self.assertIn("data-sort-nosort", panel)
        self.assertIn('data-sort-type="{{ sort_type }}"', panel)
        self.assertIn("_anchor='team-page-stats'", panel)
        self.assertIn("data-sort-value=\"{{ pl.full_name|lower }}\"", panel)
        self.assertIn("data-sort-value=\"{{ pl.overall_ability", panel)
        self.assertIn("data-sort-value=\"{{ pl.overall_potential", panel)
        self.assertIn("th_sk('abi'", panel)
        self.assertIn("th_g('abi'", panel)
        self.assertNotIn("ABI and POT are not sort links", panel)
        self.assertIn('th.addEventListener("mousedown"', js)
        self.assertIn("initStatsTableScrollPreserve", js)
        self.assertIn("bowl-stats-table-scroll", js)


if __name__ == "__main__":
    unittest.main()
