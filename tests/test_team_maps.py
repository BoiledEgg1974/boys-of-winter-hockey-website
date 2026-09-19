"""Tests for Discord team emote maps."""
from __future__ import annotations

import unittest

from scripts.league_discord_bot.team_maps import (
    FANTASY_TEAMS,
    HISTORICAL_TEAMS,
    _compile_team_map,
    emoji_for_abbrev,
    entry_for_fhm_team_id,
    fhm_team_id_for_abbrev,
    fhm_team_id_for_custom_emoji_mention,
    fhm_team_id_from_message_token,
    team,
    team_emoji_prefix,
)


class TeamMapsHistoricalTest(unittest.TestCase):
    def test_updated_and_new_historical_emotes(self) -> None:
        self.assertEqual(HISTORICAL_TEAMS[3], ("TOR", "<:TOR:1527136626579476610>"))
        self.assertEqual(HISTORICAL_TEAMS[120], ("CAL", "<:CAL:1527136600721719487>"))
        self.assertEqual(HISTORICAL_TEAMS[130], ("BUF", "<:BUF:1523803268244049930>"))
        self.assertEqual(HISTORICAL_TEAMS[131], ("VAN", "<:VAN:1523803266784301056>"))

    def test_oak_alias_resolves_to_california(self) -> None:
        self.assertEqual(fhm_team_id_for_abbrev("bowl-historical", "OAK"), 120)
        self.assertEqual(fhm_team_id_for_abbrev("bowl-historical", "CAL"), 120)
        self.assertEqual(
            emoji_for_abbrev("bowl-historical", "OAK"),
            "<:CAL:1527136600721719487>",
        )
        self.assertEqual(
            team_emoji_prefix("bowl-historical", {"team_abbrev": "OAK"}),
            "<:CAL:1527136600721719487> ",
        )

    def test_lookup_by_fhm_id_and_snowflake(self) -> None:
        entry = entry_for_fhm_team_id("bowl-historical", 130)
        self.assertEqual(entry, ("BUF", "<:BUF:1523803268244049930>"))
        self.assertEqual(
            fhm_team_id_for_custom_emoji_mention(
                "bowl-historical", "<:BUF:1523803268244049930>"
            ),
            130,
        )
        self.assertEqual(
            fhm_team_id_from_message_token("bowl-historical", "VAN"),
            131,
        )

    def test_cap_emoji_name_override(self) -> None:
        # Display abbrev WAS / NAS; Discord custom emoji names are WSH / NSH.
        self.assertEqual(
            emoji_for_abbrev("bowl-cap", "WAS"),
            "<:WSH:1429890537913188352>",
        )
        self.assertEqual(
            emoji_for_abbrev("bowl-cap", "NAS"),
            "<:NSH:1470179048859767068>",
        )

    def test_relegation_emotes_compile(self) -> None:
        self.assertEqual(FANTASY_TEAMS[0], ("SRK", "<:SRK:1549541031551176714>"))
        self.assertEqual(FANTASY_TEAMS[7], ("SEO", "<:SEO:1549540932507009054>"))
        self.assertEqual(emoji_for_abbrev("bowl-fantasy", "SRK"), "<:SRK:1549541031551176714>")

    def test_compile_accepts_legacy_mention_tuples(self) -> None:
        teams, index = _compile_team_map(
            {
                1: team("AAA", "111"),
                2: ("BBB", "<:BBB:222222222222222222>"),
            }
        )
        self.assertEqual(teams[1], ("AAA", "<:AAA:111>"))
        self.assertEqual(teams[2], ("BBB", "<:BBB:222222222222222222>"))
        self.assertEqual(index["BBB"], 2)


if __name__ == "__main__":
    unittest.main()
