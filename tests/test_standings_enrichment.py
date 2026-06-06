"""Standings enrichment helpers."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.standings_enrichment import (
    build_standings_power_rankings,
    build_standings_row_context,
)


class StandingsEnrichmentTest(unittest.TestCase):
    @patch("app.services.standings_enrichment.build_postseason_odds_payload")
    @patch("app.services.standings_enrichment.recent_form_last10_map")
    @patch("app.services.standings_enrichment.team_momentum_streak_label_from_games")
    @patch("app.services.standings_enrichment.build_standings_power_rankings")
    def test_row_context_includes_playoff_and_trend(
        self,
        mock_pr,
        mock_streak,
        mock_form,
        mock_po,
    ) -> None:
        session = MagicMock()
        st = MagicMock()
        st.team_id = 7
        tm = MagicMock()
        tm.slug = "tor"
        st.team = tm
        mock_form.return_value = {7: {"last10": "6-4", "last10_wins": 6, "last10_losses": 4}}
        mock_streak.return_value = ("W", 3)
        mock_po.return_value = {"by_slug": {"tor": {"playoffs": 0.82}}}
        mock_pr.return_value = [{"slug": "tor", "trend_dir": "up"}]

        out = build_standings_row_context(
            session,
            season_id=1,
            standings_rows=[st],
            league_slug="bowl-cap",
        )
        self.assertEqual(out[7]["last10"], "6-4")
        self.assertEqual(out[7]["playoff_pct"], 0.82)
        self.assertEqual(out[7]["trend_dir"], "up")

    @patch("app.services.standings_enrichment.apply_power_rank_trends")
    @patch("app.services.standings_enrichment.select_power_rank_baseline_map")
    @patch("app.services.standings_enrichment.compute_power_rankings_payload")
    def test_power_rankings_applies_trends(
        self,
        mock_compute,
        mock_baseline,
        mock_apply,
    ) -> None:
        mock_compute.return_value = {"teams": [{"slug": "ana", "rank": 1}]}
        mock_baseline.return_value = {}
        rows = build_standings_power_rankings(
            MagicMock(),
            season_id=1,
            league_slug="bowl-cap",
        )
        self.assertEqual(rows[0]["slug"], "ana")
        mock_apply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
