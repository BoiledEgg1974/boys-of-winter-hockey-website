"""Relegation record scope helpers."""
from __future__ import annotations

import unittest

from app.services.relegation_records import (
    RELEGATION_TIER_DISCORD_EMOTES,
    normalize_record_snapshot_key,
    record_snapshot_scope_prefix,
)


class RelegationRecordsScopeTest(unittest.TestCase):
    def test_snapshot_prefix_per_scope(self) -> None:
        self.assertEqual(record_snapshot_scope_prefix("upper"), "rec:upper:")
        self.assertEqual(record_snapshot_scope_prefix("combined"), "rec:combined:")

    def test_legacy_snapshot_key_normalized_to_combined(self) -> None:
        key = normalize_record_snapshot_key(
            "season:league:rs:goals", league_slug="bowl-fantasy"
        )
        self.assertEqual(key, "rec:combined:season:league:rs:goals")

    def test_emote_ids_present(self) -> None:
        self.assertIn("<:BLUP:", RELEGATION_TIER_DISCORD_EMOTES["upper"])
        self.assertIn("<:BLOW:", RELEGATION_TIER_DISCORD_EMOTES["lower"])


if __name__ == "__main__":
    unittest.main()
