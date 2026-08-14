"""Hockey Discord events must not fan out to other leagues' servers."""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.discord_events import (
    DISCORD_CHANNEL_FANOUT_EVENT_KEYS,
    _idempotency_is_extra_fanout_slot,
    delivery_discord_channel_ids,
    event_key_allows_channel_fanout,
    route_discord_channel_ids,
)


def _route(*, c1: str = "", c2: str = "", c3: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        discord_channel_id=c1,
        discord_channel_id_2=c2,
        discord_channel_id_3=c3,
    )


class ChannelFanoutPolicyTest(unittest.TestCase):
    def test_racing_keys_fan_out(self) -> None:
        self.assertTrue(event_key_allows_channel_fanout("race_results"))
        self.assertTrue(event_key_allows_channel_fanout("heat_results"))
        self.assertTrue(event_key_allows_channel_fanout("circuit_standings_update"))
        self.assertEqual(
            DISCORD_CHANNEL_FANOUT_EVENT_KEYS,
            {"race_results", "heat_results", "circuit_standings_update"},
        )

    def test_record_broken_stays_on_primary_channel(self) -> None:
        route = _route(
            c1="111111111111111111",
            c2="222222222222222222",
            c3="333333333333333333",
        )
        self.assertEqual(len(route_discord_channel_ids(route)), 3)
        self.assertEqual(
            delivery_discord_channel_ids(route, "record_broken"),
            ["111111111111111111"],
        )
        self.assertFalse(event_key_allows_channel_fanout("record_broken"))
        self.assertFalse(event_key_allows_channel_fanout("game_boxscore"))
        self.assertFalse(event_key_allows_channel_fanout("bowl_six_leaders_update"))

    def test_racing_uses_all_configured_slots(self) -> None:
        route = _route(c1="111111111111111111", c2="222222222222222222")
        self.assertEqual(
            delivery_discord_channel_ids(route, "race_results"),
            ["111111111111111111", "222222222222222222"],
        )

    def test_extra_slot_idempotency(self) -> None:
        self.assertFalse(_idempotency_is_extra_fanout_slot("bowl-cap:record_broken:abc"))
        self.assertFalse(_idempotency_is_extra_fanout_slot("sid:ch0"))
        self.assertTrue(_idempotency_is_extra_fanout_slot("sid:ch1"))
        self.assertTrue(_idempotency_is_extra_fanout_slot("season:goals:player:2:55:ch2"))


if __name__ == "__main__":
    unittest.main()
