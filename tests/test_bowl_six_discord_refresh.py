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
    def test_discord_poll_advances_slates_and_refreshes_current_leaders(self) -> None:
        app = Flask(__name__)
        app.config["BOWL_SIX_DISCORD_REFRESH_INTERVAL_SECONDS"] = 60
        _BOWL_SIX_DISCORD_REFRESH_LAST.clear()

        with app.app_context(), patch(
            "app.services.bowl_six.refresh_bowl_six_leaders_for_discord_poll"
        ) as leaders_refresh, patch(
            "app.services.bowl_six.maybe_enqueue_bowl_six_roster_reminders"
        ) as reminders, patch(
            "app.routes.api.commit_with_sqlite_retry"
        ):
            _refresh_bowl_six_discord_triggers("bowl-cap")
            _refresh_bowl_six_discord_triggers("bowl-cap")

        leaders_refresh.assert_called_once()
        reminders.assert_called_once()

    def test_discord_poll_same_path_for_all_league_slugs(self) -> None:
        app = Flask(__name__)
        app.config["BOWL_SIX_DISCORD_REFRESH_INTERVAL_SECONDS"] = 0
        _BOWL_SIX_DISCORD_REFRESH_LAST.clear()

        with app.app_context(), patch(
            "app.services.bowl_six.refresh_bowl_six_leaders_for_discord_poll"
        ) as leaders_refresh, patch(
            "app.services.bowl_six.maybe_enqueue_bowl_six_roster_reminders"
        ), patch(
            "app.routes.api.commit_with_sqlite_retry"
        ):
            for slug in ("bowl-cap", "bowl-fantasy", "bowl-historical"):
                _refresh_bowl_six_discord_triggers(slug)

        self.assertEqual(
            [call.args[2] for call in leaders_refresh.call_args_list],
            ["bowl-cap", "bowl-fantasy", "bowl-historical"],
        )


if __name__ == "__main__":
    unittest.main()
