"""AP redemption per-item GM form fields."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.ap_redemption_forms import (
    catalog_item_form_key,
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
            {"general": ["general"]},
            session=MagicMock(),
        )
        self.assertIsNone(err)
        self.assertEqual(details.get("scope"), "general")

        details2, err2 = parse_catalog_item_details(
            "injury_proneness",
            {"body_part": "Knee"},
            session=MagicMock(),
        )
        self.assertIsNone(err2)
        self.assertEqual(details2.get("body_part"), "Knee")

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
