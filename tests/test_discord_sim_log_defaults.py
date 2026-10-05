"""Env-backed defaults for hockey sim-log Discord routes."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.discord_events import (
    GM_EXPORT_TRACKER_POLL_EVENT_KEY,
    SIM_CYCLE_UPDATE_EVENT_KEY,
    _normalize_hockey_sim_log_discord_routes,
)


class HockeySimLogDiscordDefaultsTests(unittest.TestCase):
    def test_normalize_fills_guild_and_sim_log_from_env(self) -> None:
        session = MagicMock()
        cfg = MagicMock()
        cfg.guild_id = ""
        cfg.is_enabled = True
        sim_route = MagicMock(
            discord_channel_id="",
            is_enabled=True,
            channel_key="sim-log",
        )
        tracker_route = MagicMock(
            discord_channel_id="",
            is_enabled=True,
            channel_key="gm-export-tracker",
        )

        with patch(
            "app.services.discord_events._ensure_discord_bot_cfg_row",
            return_value=cfg,
        ), patch(
            "app.services.discord_events._suppressed_default_route_keys",
            return_value=set(),
        ), patch(
            "app.services.discord_events._route_map",
            return_value={
                SIM_CYCLE_UPDATE_EVENT_KEY: sim_route,
                GM_EXPORT_TRACKER_POLL_EVENT_KEY: tracker_route,
            },
        ), patch(
            "app.config.default_discord_guild_id_for_league",
            return_value="1201286402046955580",
        ), patch(
            "app.config.discord_sim_log_channel_id",
            return_value="123456789012345678",
        ), patch(
            "app.config.discord_gm_export_tracker_channel_id",
            return_value="234567890123456789",
        ):
            changed = _normalize_hockey_sim_log_discord_routes(session, "bowl-fantasy")

        self.assertTrue(changed)
        self.assertEqual(cfg.guild_id, "1201286402046955580")
        self.assertEqual(sim_route.discord_channel_id, "123456789012345678")
        self.assertEqual(tracker_route.discord_channel_id, "234567890123456789")

    def test_normalize_noop_for_racing_league(self) -> None:
        session = MagicMock()
        self.assertFalse(
            _normalize_hockey_sim_log_discord_routes(session, "bowl-formula")
        )


if __name__ == "__main__":
    unittest.main()
