"""Template guards for advanced stats surfaces."""
from __future__ import annotations

import unittest
from pathlib import Path


class AdvancedStatsTemplatesTest(unittest.TestCase):
    def test_advanced_stats_hub_template(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "advanced_stats.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        for marker in (
            "advanced-stats-team-chart",
            "Team Analytics Map",
            "advanced-stats-team-chart-data",
            "data-team-chart-season",
            "advanced-stats-hub-controls",
            "advanced-stats-tabset",
            "advanced-stats-tabset--seven",
            "panel--luck",
            "panel--discipline",
            "panel--lines",
            "panel--shot_quality",
            "advanced-stats-lines__filters",
            'name="tab" value="lines"',
            '<option value=""{% if line_team_id is none %} selected{% endif %}>All teams</option>',
            "Current FHM line combinations",
            "Shot Share Proxy",
            "High-Danger SQ%",
            "SQ Avg",
            "PTS/60",
            'data-sort-value="{% if row.gsaa is not none %}',
            "Team from the season stat row",
            "distinct imported game logs",
            "sorted by heat factor",
            "advanced-stats-band--{{ row.pdo_band }}",
            'data-page-size="50"',
            "Points Above Point Per Game",
            "advanced-stats-division-chart__svg",
            "data-division-chart-team",
            "data-division-chart-logo",
            "archive_year",
        ):
            self.assertIn(marker, template)
        self.assertIn(".process-stats__sq-profile", css)
        self.assertIn(".advanced-stats-team-chart", css)
        self.assertIn(".advanced-stats-team-chart__point", css)
        self.assertIn(".advanced-stats-team-chart-tooltip", css)
        self.assertIn(".advanced-stats-tabset", css)
        self.assertIn(".advanced-stats-tabset--seven", css)
        self.assertIn(".advanced-stats-hub-controls", css)
        self.assertIn(".team-shot-quality", css)
        self.assertIn(".advanced-stats-lines__filters", css)
        self.assertIn(".advanced-stats-band--hot", css)
        self.assertIn(".advanced-stats-band--neutral", css)
        self.assertIn(".advanced-stats-band--cold", css)
        self.assertIn(".advanced-stats-division-chart__line", css)
        self.assertIn(".advanced-stats-division-tooltip", css)
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn("initPaginatedTable", js)
        self.assertIn("Page 1 of ", js)
        self.assertIn("initAdvancedStatsTeamChart", js)
        self.assertIn("initAdvancedStatsDivisionTooltips", js)

    def test_advanced_stats_route_includes_lines_tab(self) -> None:
        root = Path(__file__).resolve().parents[1]
        main = (root / "app" / "routes" / "main.py").read_text(encoding="utf-8")
        team_template = (root / "app" / "templates" / "team.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        self.assertIn('"lines", "label": "Lines"', main)
        self.assertIn('"shot_quality", "label": "Shot Quality"', main)
        self.assertIn("build_line_stats_rows", main)
        self.assertIn("build_advanced_stats_season_options", main)
        self.assertIn("load_archived_advanced_stats_hub", main)
        self.assertIn("build_team_analytics_chart_archive", main)
        self.assertIn("build_team_player_analytics_archive", main)
        self.assertIn("build_team_player_trends_archive", main)
        self.assertIn("build_team_stats_trends_archive", main)
        self.assertIn("build_team_shot_quality_payload", main)
        self.assertIn("team-player-analytics", team_template)
        self.assertIn("Player Analytics Map", team_template)
        self.assertIn('_team_shot_quality.html', team_template)
        team_sq = (root / "app" / "templates" / "_team_shot_quality.html").read_text(encoding="utf-8")
        self.assertIn("team-shot-quality", team_sq)
        self.assertIn("Quality-weighted average", team_sq)
        self.assertIn("team-player-analytics-data", team_template)
        self.assertIn("team-player-analytics__watermark", team_template)
        self.assertIn("team-stats-trends", team_template)
        self.assertIn("Team Stats Trend Lines", team_template)
        self.assertIn("team-stats-trends-data", team_template)
        self.assertIn("data-team-stats-trend-situation", team_template)
        self.assertIn("--team-stats-trend-line", team_template)
        idx_stats = team_template.index("team-stats-trends")
        idx_tabs = team_template.index('id="team-management-tabs"')
        self.assertLess(idx_stats, idx_tabs)
        self.assertIn("team-player-trends", team_template)
        self.assertIn("Player Trend Lines", team_template)
        self.assertIn("team-player-trends-data", team_template)
        self.assertIn("data-team-player-trend-chart", team_template)
        self.assertIn("data-team-logo-url", team_template)
        self.assertIn("initTeamPlayerAnalyticsCharts", js)
        self.assertIn("initTeamPlayerTrendCharts", js)
        self.assertIn("initTeamStatsTrendCharts", js)
        self.assertIn("team-stats-trends__watermark", js)
        self.assertIn("team-stats-trends__latest-logo", js)
        self.assertIn("Number(pt.game_number || 0) <= maxGame", js)
        self.assertIn("metricHasData", js)
        self.assertIn("team-player-trends__watermark", js)
        self.assertIn("datasetGameCount", js)
        self.assertIn("Math.min(datasetGameCount, 82)", js)
        self.assertIn("displayGameNumber", js)
        self.assertIn("chartSvg.onpointermove", js)
        self.assertIn("team-player-trends__label-value", js)
        self.assertIn(".team-player-analytics-tooltip", css)
        self.assertIn(".team-player-analytics__watermark", css)
        self.assertIn(".team-stats-trends__chart", css)
        self.assertIn(".team-stats-trends__watermark", css)
        self.assertIn(".team-stats-trends__latest-logo", css)
        self.assertIn(".team-player-trends__chart", css)
        self.assertIn(".team-player-trends__tooltip", css)
        self.assertIn(".team-player-trends__watermark", css)
        self.assertIn(".team-player-trends__label-value", css)
        self.assertIn('active_tab = "lines"', main)

    def test_player_process_profile_partial(self) -> None:
        partial = (Path(__file__).resolve().parents[1] / "app" / "templates" / "_player_process_profile.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Process Profile", partial)
        self.assertIn("not expected goals or shot maps", partial)
        self.assertIn("Season Process", partial)
        self.assertIn("Shot Share Detail", partial)
        self.assertIn("Game Event Profile", partial)
        self.assertIn("Zone Starts", partial)
        self.assertIn("Shot Quality Mix", partial)
        self.assertIn("Imported FHM shot-quality buckets", partial)
        self.assertIn("PTS/60", partial)
        self.assertIn("GF/60", partial)
        self.assertIn("GA/60", partial)
        self.assertIn("PP PTS/60", partial)
        self.assertIn("CF% rel", partial)
        self.assertIn("High-danger share (SQ3+SQ4)", partial)
        self.assertIn("Corsi For percentage", partial)
        self.assertIn("Fenwick For percentage", partial)
        self.assertIn("Penalty minutes", partial)
        self.assertIn("Goals Saved Above Average", partial)
        self.assertIn("Save percentage", partial)
        self.assertIn("GAA/60", partial)
        self.assertIn("Season Volume", partial)
        self.assertIn("Game Log Profile", partial)
        self.assertIn("not expected goals against or save maps", partial)
        advanced = (Path(__file__).resolve().parents[1] / "app" / "templates" / "advanced_stats.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("advanced-stats-tabset__panel--goalies", advanced)
        self.assertIn("Win-loss-overtime loss record", advanced)
        self.assertIn("Shutouts from the FHM goalie stat export", advanced)
        self.assertIn("Average imported FHM game rating", advanced)
        sq_partial = (
            Path(__file__).resolve().parents[1] / "app" / "templates" / "_sq_profile_bars.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Shot quality bucket 0", sq_partial)
        self.assertIn("Shot quality bucket 4", sq_partial)

    def test_game_flow_partial(self) -> None:
        partial = (Path(__file__).resolve().parents[1] / "app" / "templates" / "_game_flow_card.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Game Flow", partial)
        self.assertIn("Shots by Period", partial)

    def test_team_depth_draft_picks_use_compact_layout(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "team.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        self.assertIn("team-depth-draft-picks-compact", template)
        self.assertIn(".team-depth-draft-picks-compact__row", css)

    def test_team_depth_prospects_are_paginated(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "team.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        main = (root / "app" / "routes" / "main.py").read_text(encoding="utf-8")
        self.assertIn('data-team-depth-prospects-page-size="10"', template)
        self.assertIn("Page 1 of 1", template)
        self.assertIn("2.0+ potential", template)
        self.assertIn("potential < 2.0", main)
        self.assertIn("data-team-depth-prospects-status", js)
        self.assertIn(".team-depth-prospects-pager", css)


if __name__ == "__main__":
    unittest.main()
