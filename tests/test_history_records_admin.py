"""Historical records admin: navigation, templates, DB awards, all-star upsert."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.services.admin_history_records import (
    ALL_STAR_SLOT_DEFAULTS,
    award_name_choices_from_names,
    award_matches_season_label,
    merge_sheet_season_notes,
    sheet_season_from_notes,
)


class HistoryRecordsAdminTemplateTest(unittest.TestCase):
    def test_admin_home_links_history_records(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_site_home.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("admin_history_records_home", text)
        self.assertIn("Historical records editor", text)

    def test_team_seasons_template_has_dropdowns(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_history_team_seasons.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn('name="team_id"', text)
        self.assertIn('action" value="delete"', text)

    def test_awards_template_has_player_dropdown(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_history_awards.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn('name="player_id"', text)
        self.assertIn('<select name="award_name" required>', text)

    def test_all_stars_template_has_six_slots(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_history_all_stars.html"
        text = path.read_text(encoding="utf-8")
        self.assertEqual(len(ALL_STAR_SLOT_DEFAULTS), 6)
        self.assertIn('name="player_id_{{ slot_num }}"', text)
        self.assertIn("slot_defaults", text)


class HistoryRecordsServiceTest(unittest.TestCase):
    def test_merge_sheet_season_notes(self) -> None:
        out = merge_sheet_season_notes("no_winner=1", "1989-90")
        self.assertEqual(sheet_season_from_notes(out), "1989-90")
        self.assertIn("no_winner=1", out)

    def test_award_matches_season_label(self) -> None:
        class _Award:
            notes = "sheet_season=1968-69; no_winner=1"
            season = None

        self.assertTrue(award_matches_season_label(_Award(), "1968-69"))
        self.assertFalse(award_matches_season_label(_Award(), "1969-70"))

    def test_team_records_awards_use_db_helper(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "services" / "team_records.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("list_awards_for_season_label", text)
        self.assertNotIn("read_csv_normalized(raw_csv)", text)

    def test_award_dropdown_choices_are_deduped(self) -> None:
        choices = award_name_choices_from_names(
            ["WILLIAM JENNINGS TROPHY", " WILLIAM   JENNINGS TROPHY ", "Hart Memorial Trophy"]
        )
        self.assertEqual(choices, ["Hart Memorial Trophy", "WILLIAM JENNINGS TROPHY"])


class HistoryRecordsImportSafetyTest(unittest.TestCase):
    def test_runner_protects_admin_awards_on_replace_all(self) -> None:
        path = Path(__file__).resolve().parents[1] / "scripts" / "import_pipeline" / "runner.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("delete_non_admin_history_awards", text)
        self.assertNotIn("delete(HistoryAward))", text.split("if replace_all:")[1].split("elif needle")[0])

    def test_all_stars_import_keeps_admin_rows(self) -> None:
        path = Path(__file__).resolve().parents[1] / "scripts" / "import_pipeline" / "runner.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("delete_non_admin_all_stars", text)

    def test_team_season_import_keeps_admin_rows(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "scripts"
            / "import_pipeline"
            / "team_season_records_loader.py"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("delete_non_admin_team_season_records", text)


if __name__ == "__main__":
    unittest.main()
