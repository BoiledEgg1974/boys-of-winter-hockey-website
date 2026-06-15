"""BOWL Six Discord poll refresh throttling."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from flask import Flask

from app.routes.api import (
    _BOWL_SIX_DISCORD_REFRESH_LAST,
    _refresh_bowl_six_discord_triggers,
)


class BowlSixDiscordRefreshTests(unittest.TestCase):
    def test_discord_pending_refresh_is_throttled_per_league(self) -> None:
        app = Flask(__name__)
        app.config["BOWL_SIX_DISCORD_REFRESH_INTERVAL_SECONDS"] = 60
        _BOWL_SIX_DISCORD_REFRESH_LAST.clear()

        with app.app_context(), patch(
            "app.services.bowl_six.auto_update_bowl_six_slates"
        ) as auto_update, patch(
            "app.services.bowl_six.maybe_enqueue_bowl_six_roster_reminders"
        ) as reminders, patch(
            "app.routes.api.commit_with_sqlite_retry"
        ):
            _refresh_bowl_six_discord_triggers("bowl-cap")
            _refresh_bowl_six_discord_triggers("bowl-cap")

        auto_update.assert_called_once()
        reminders.assert_called_once()


if __name__ == "__main__":
    unittest.main()
