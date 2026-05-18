"""AP redemption per-item GM form fields."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.ap_redemption_forms import (
    catalog_item_form_key,
    catalog_item_has_detail_form,
    format_details_summary,
    parse_catalog_item_details,
)


class ApRedemptionFormsTest(unittest.TestCase):
    def test_catalog_title_maps_to_form_key(self):
        self.assertEqual(catalog_item_form_key("Change a Rival"), "change_rival")
        self.assertEqual(
            catalog_item_form_key("Purchase a Silver Boost for one of your Draftees."),
            "silver_draft_boost",
        )
        self.assertEqual(
            catalog_item_form_key("Re-Allocate 1 Point from Any Attribute"),
            "reallocate_attribute",
        )
        self.assertEqual(
            catalog_item_form_key("Increase Development Speed by 2 points"),
            "development_speed",
        )
        self.assertEqual(
            catalog_item_form_key("Slow aging by 2 points"),
            "slow_aging",
        )
        self.assertTrue(
            catalog_item_has_detail_form(
                catalog_item_form_key("Add 2 Points to Coach's Attribute")
            )
        )

    def test_market_fan_media_requires_choice(self):
        details, err = parse_catalog_item_details(
            "market_fan_media",
            {"choices": []},
            session=MagicMock(),
        )
        self.assertIsNone(details)
        self.assertIn("Select", err or "")

    def test_injury_proneness_general_or_body(self):
        details, err = parse_catalog_item_details(
            "injury_proneness",
            {"player_name": "John Smith", "general": ["general"]},
            session=MagicMock(),
        )
        self.assertIsNone(err)
        self.assertEqual(details.get("scope"), "general")
        self.assertEqual(details.get("player_name"), "John Smith")

        details2, err2 = parse_catalog_item_details(
            "injury_proneness",
            {"player_name": "Jane Doe", "body_part": "Knee"},
            session=MagicMock(),
        )
        self.assertIsNone(err2)
        self.assertEqual(details2.get("body_part"), "Knee")

    def test_player_target_redemptions_require_name(self):
        details, err = parse_catalog_item_details(
            "development_speed",
            {"player_name": "Alex Ovechkin"},
            session=MagicMock(),
        )
        self.assertIsNone(err)
        self.assertIn("Alex", format_details_summary(details))

        details2, err2 = parse_catalog_item_details(
            "reallocate_attribute",
            {
                "player_name": "Sidney Crosby",
                "from_attribute": "Speed",
                "to_attribute": "Strength",
            },
            session=MagicMock(),
        )
        self.assertIsNone(err2)
        self.assertIn("Sidney", format_details_summary(details2))

        _, err3 = parse_catalog_item_details(
            "coach_attribute_points",
            {
                "coach_roles": ["coach"],
                "attribute": "Motivation",
            },
            session=MagicMock(),
        )
        self.assertIn("Coach/GM", err3 or "")

    def test_fantasy_commissioner_and_created_player_forms(self):
        self.assertTrue(catalog_item_has_detail_form(catalog_item_form_key("Relocate Your Team")))
        self.assertTrue(
            catalog_item_has_detail_form(
                catalog_item_form_key("Create a 5-Star Potential Player")
            )
        )
        details, err = parse_catalog_item_details(
            "relocate_team",
            {"ack": "1"},
            session=MagicMock(),
        )
        self.assertIsNone(err)
        self.assertTrue(details.get("commissioner_followup"))

        details2, err2 = parse_catalog_item_details(
            "reclassify_created_player",
            {"from_position": "center", "to_position": "left_wing"},
            session=MagicMock(),
        )
        self.assertIsNone(err2)
        self.assertEqual(details2.get("from_position_label"), "Center")

    def test_change_rival_resolves_team_name(self):
        team = MagicMock()
        team.full_display_name.return_value = "Boston Bruins"
        session = MagicMock()
        session.get.return_value = team
        details, err = parse_catalog_item_details(
            "change_rival",
            {"rival_team_id": "12"},
            session=session,
        )
        self.assertIsNone(err)
        self.assertEqual(details["rival_team_name"], "Boston Bruins")
        summary = format_details_summary(details)
        self.assertIn("Boston", summary)


if __name__ == "__main__":
    unittest.main()
