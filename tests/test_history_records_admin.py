"""Historical records admin: navigation, templates, DB awards, all-star upsert."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.services.admin_history_records import (
    ALL_STAR_SLOT_DEFAULTS,
    apply_gm_winner_to_award_notes,
    award_name_choices_from_names,
    award_matches_season_label,
    gm_user_id_from_award_notes,
    merge_sheet_season_notes,
    sheet_season_from_notes,
    staff_award_winner_admin_label,
)


class HistoryRecordsAdminTemplateTest(unittest.TestCase):
    def test_admin_home_links_records_editor(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_site_home.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("admin_records_home", text)
        self.assertIn("Records editor", text)

    def test_admin_home_links_history_records(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_site_home.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("admin_records_home", text)

    def test_team_seasons_template_has_dropdowns(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_history_team_seasons.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn('name="team_id"', text)
        self.assertIn('name="shots_for"', text)
        self.assertIn('action" value="delete"', text)

    def test_admin_home_links_season_awards(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_site_home.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("admin_awards", text)
        self.assertIn("Season Awards", text)

    def test_unified_awards_template_has_all_star_slots(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "templates" / "admin_awards.html"
        text = path.read_text(encoding="utf-8")
        self.assertIn("First Team", text)
        self.assertIn("Second Team", text)
        self.assertIn('name="award_name"', text)
        self.assertIn('name="gm_user_id"', text)
        self.assertIn("player_id_{{ team_rank }}_{{ slot_num }}", text)

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

    def test_apply_gm_winner_jim_gregory_notes(self) -> None:
        out = apply_gm_winner_to_award_notes(
            "JIM GREGORY TROPHY",
            "sheet_season=1999-00",
            gm_username="Mark1",
            gm_display="Mark1",
            gm_user_id=42,
        )
        self.assertIn("unresolved_team=Mark1", out)
        self.assertIn("gm_user_id=42", out)
        self.assertIn("display_name=Mark1", out)
        self.assertIn("sheet_season=1999-00", out)
        self.assertNotIn("unresolved_player=", out)

    def test_apply_gm_winner_jack_adams_notes(self) -> None:
        out = apply_gm_winner_to_award_notes(
            "JACK ADAMS TROPHY",
            None,
            gm_username="Mark1",
            gm_display="Mark1",
            gm_user_id=7,
        )
        self.assertIn("unresolved_player=Mark1", out)
        self.assertNotIn("unresolved_team=", out)

    def test_gm_user_id_from_notes_round_trip(self) -> None:
        notes = apply_gm_winner_to_award_notes(
            "JIM GREGORY TROPHY",
            None,
            gm_username="Skyvendrake",
            gm_display="Sky",
            gm_user_id=99,
        )
        self.assertEqual(gm_user_id_from_award_notes(notes), 99)

    def test_staff_award_winner_admin_label_from_notes(self) -> None:
        from app.models import HistoryAward

        award = HistoryAward(
            award_name="JIM GREGORY TROPHY",
            staff_fhm_id="Mark1",
            notes="sheet_season=1999-00; unresolved_team=Mark1",
        )
        self.assertEqual(staff_award_winner_admin_label(award), "Mark1")


class HistoryRecordsImportSafetyTest(unittest.TestCase):
    def test_runner_protects_admin_awards_on_replace_all(self) -> None:
        path = Path(__file__).resolve().parents[1] / "scripts" / "import_pipeline" / "runner.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("delete_non_admin_history_awards", text)
        self.assertIn("delete_non_admin_history_awards_matching", text)
        self.assertIn("admin_history_award_slot_keys", text)
        self.assertNotIn("delete(HistoryAward))", text.split("if replace_all:")[1].split("elif needle")[0])

    def test_all_stars_import_is_additive_upsert(self) -> None:
        path = Path(__file__).resolve().parents[1] / "scripts" / "import_pipeline" / "runner.py"
        text = path.read_text(encoding="utf-8")
        # Importer must not wipe non-admin rows (Hall-of-Fame-style additive upsert).
        self.assertNotIn("delete_non_admin_all_stars", text)
        self.assertIn("HISTORY_SOURCE_ADMIN", text.split("def import_history_all_stars")[1].split("def import_trade_log")[0])

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
