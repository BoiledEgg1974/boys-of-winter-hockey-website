"""Draft Eligible GM CSV export."""
from __future__ import annotations

import csv
import io
import unittest
from unittest.mock import patch

from app import create_app
from app.config import make_league_config


class DraftEligibleCsvExportTest(unittest.TestCase):
    def test_csv_requires_gm_or_admin(self) -> None:
        for slug in ("bowl-cap", "bowl-historical", "bowl-fantasy"):
            with self.subTest(slug=slug):
                app = create_app(make_league_config(slug))
                with app.test_client() as client:
                    r = client.get("/draft-eligible.csv")
                    self.assertEqual(r.status_code, 403)

    def test_csv_returns_attachment_for_gm(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        with app.test_client() as client:
            with patch(
                "app.routes.main._can_export_draft_eligible_csv",
                return_value=True,
            ), patch(
                "app.routes.main._build_draft_eligible_rows",
                return_value=[],
            ), patch(
                "app.routes.main._draft_eligible_params_for_page",
                return_value=(type("P", (), {"timeline_year": 2026})(), "Cap draft-year rules"),
            ):
                r = client.get("/draft-eligible.csv?sort=rank&order=asc")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.content_type)
        self.assertIn('attachment; filename="draft-eligible-bowl-cap-2026.csv"', r.headers.get("Content-Disposition", ""))
        rows = list(csv.DictReader(io.StringIO(r.get_data(as_text=True))))
        self.assertEqual(rows, [])

    def test_download_button_visible_for_gm_context(self) -> None:
        app = create_app(make_league_config("bowl-historical"))
        with app.app_context():
            with app.test_request_context("/draft-eligible"):
                from flask import render_template

                html = render_template(
                    "draft_eligible.html",
                    active_tab="eligible",
                    prospect_rows=[],
                    total_prospects=0,
                    prospect_page_limit=100,
                    prospect_expanded=False,
                    prospect_overview_headers=(),
                    position=None,
                    q="",
                    prospect_sort="rank",
                    prospect_order="asc",
                    prospect_sort_desc_defaults=frozenset(),
                    player_overall_by_id={},
                    prospect_projection_headers=(),
                    prospect_projection_footnote="",
                    eligibility_params=type("P", (), {"timeline_year": 1968})(),
                    eligibility_params_source="Historical amateur pool",
                    eligibility_summary="Eligible pool summary",
                    eligibility_notes=[],
                    active_draft=None,
                    league_display="BOWL-Historical",
                    mock_draft_rows=[],
                    gm_membership=object(),
                    site_can_process_trades=False,
                )
        self.assertIn("Download CSV", html)
        self.assertIn("/draft-eligible.csv", html)


if __name__ == "__main__":
    unittest.main()
