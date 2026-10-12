"""Trophy Case Discord channel map for BOWL-Relegation."""
from __future__ import annotations

import unittest

from app.services.discord_trophy_routes import (
    RELEGATION_TROPHY_CHANNEL_SPECS,
    _OBSOLETE_TROPHY_CHANNEL_KEYS,
)


class DiscordTrophyRoutesTest(unittest.TestCase):
    def test_specs_include_blup_and_blow_without_lady_byng(self) -> None:
        keys = {spec[1] for spec in RELEGATION_TROPHY_CHANNEL_SPECS}
        self.assertEqual(len(keys), 24)
        self.assertIn("blup-art-ross", keys)
        self.assertIn("blow-crosby", keys)
        self.assertNotIn("blup-lady-byng", keys)
        tiers = {spec[0] for spec in RELEGATION_TROPHY_CHANNEL_SPECS}
        self.assertEqual(tiers, {"blup", "blow"})

    def test_obsolete_lady_byng_keys_listed(self) -> None:
        self.assertIn("blup-lady-byng", _OBSOLETE_TROPHY_CHANNEL_KEYS)


if __name__ == "__main__":
    unittest.main()
