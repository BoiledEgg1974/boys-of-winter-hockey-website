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
    def test_discord_poll_refreshes_leaders_without_full_auto_update_by_default(self) -> None:
        app = Flask(__name__)
        app.config["BOWL_SIX_DISCORD_REFRESH_INTERVAL_SECONDS"] = 60
        _BOWL_SIX_DISCORD_REFRESH_LAST.clear()

        with app.app_context(), patch(
            "app.services.bowl_six.auto_update_bowl_six_slates"
        ) as auto_update, patch(
            "app.services.bowl_six.refresh_bowl_six_leaders_for_discord_poll"
        ) as leaders_refresh, patch(
            "app.services.bowl_six.maybe_enqueue_bowl_six_roster_reminders"
        ) as reminders, patch(
            "app.routes.api.commit_with_sqlite_retry"
        ):
            _refresh_bowl_six_discord_triggers("bowl-cap")

        auto_update.assert_not_called()
        leaders_refresh.assert_called_once()
        reminders.assert_called_once()

    def test_discord_poll_auto_update_when_enabled(self) -> None:
        app = Flask(__name__)
        app.config["BOWL_SIX_DISCORD_REFRESH_INTERVAL_SECONDS"] = 60
        app.config["BOWL_SIX_DISCORD_POLL_AUTO_UPDATE"] = True
        _BOWL_SIX_DISCORD_REFRESH_LAST.clear()

        with app.app_context(), patch(
            "app.services.bowl_six.auto_update_bowl_six_slates"
        ) as auto_update, patch(
            "app.services.bowl_six.refresh_bowl_six_leaders_for_discord_poll"
        ) as leaders_refresh, patch(
            "app.services.bowl_six.maybe_enqueue_bowl_six_roster_reminders"
        ) as reminders, patch(
            "app.routes.api.commit_with_sqlite_retry"
        ):
            _refresh_bowl_six_discord_triggers("bowl-cap")
            _refresh_bowl_six_discord_triggers("bowl-cap")

        auto_update.assert_called_once()
        leaders_refresh.assert_not_called()
        reminders.assert_called_once()
