"""BOWL Six page loads must not rescore slates on the request thread."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from flask import Flask

from app.services.bowl_six import (
    reset_bowl_six_slate_refresh_state,
    schedule_bowl_six_slate_refresh,
)


class BowlSixPageRefreshTests(unittest.TestCase):
    def setUp(self) -> None:
        reset_bowl_six_slate_refresh_state()

    def tearDown(self) -> None:
        reset_bowl_six_slate_refresh_state()

    def test_schedule_starts_one_background_job_and_throttles(self) -> None:
        app = Flask(__name__)
        app.config["BOWL_SIX_SLATE_REFRESH_INTERVAL_SECONDS"] = 60
        started: list[object] = []

        def _fake_thread(*, target, name, daemon):
            started.append((name, daemon))
            return Mock(start=lambda: None)

        with app.app_context(), patch(
            "app.services.bowl_six.threading.Thread", side_effect=_fake_thread
        ) as thread_cls:
            self.assertTrue(schedule_bowl_six_slate_refresh("bowl-fantasy"))
            self.assertFalse(schedule_bowl_six_slate_refresh("bowl-fantasy"))

        thread_cls.assert_called_once()
        self.assertEqual(started, [("bowl-six-slate-bowl-fantasy", True)])

    def test_schedule_skips_when_auto_update_already_running(self) -> None:
        app = Flask(__name__)
        app.config["BOWL_SIX_SLATE_REFRESH_INTERVAL_SECONDS"] = 0
        from app.services import bowl_six as bowl_six_mod

        bowl_six_mod._SLATE_REFRESH_IN_FLIGHT.add("bowl-fantasy")
        with app.app_context(), patch("app.services.bowl_six.threading.Thread") as thread_cls:
            self.assertFalse(schedule_bowl_six_slate_refresh("bowl-fantasy"))
        thread_cls.assert_not_called()

    def test_auto_update_skips_second_in_flight_caller(self) -> None:
        from app.services import bowl_six as bowl_six_mod
        from app.services.bowl_six import auto_update_bowl_six_slates

        session = MagicMock()
        with patch("app.services.bowl_six.bowl_six_enabled", return_value=True), patch(
            "app.services.bowl_six._auto_update_bowl_six_slates_unlocked",
            return_value=["updated"],
        ) as unlocked:
            first = auto_update_bowl_six_slates(session, session, "bowl-fantasy")
            bowl_six_mod._SLATE_REFRESH_IN_FLIGHT.add("bowl-fantasy")
            second = auto_update_bowl_six_slates(session, session, "bowl-fantasy")

        self.assertEqual(first, ["updated"])
        self.assertEqual(second, [])
        unlocked.assert_called_once()

    def test_hub_and_leaders_schedule_refresh_instead_of_inline_update(self) -> None:
        portal = (
            Path(__file__).resolve().parents[1] / "app" / "routes" / "bowl_six_portal.py"
        ).read_text(encoding="utf-8")
        hub = portal.split("def bowl_six_hub()", 1)[1].split("def bowl_six_lineup()", 1)[0]
        leaders = portal.split("def bowl_six_leaders()", 1)[1].split(
            "def bowl_six_api_lineup_snapshot", 1
        )[0]
        self.assertIn("schedule_bowl_six_slate_refresh(slug)", hub)
        self.assertNotIn("auto_update_bowl_six_slates", hub)
        self.assertIn("schedule_bowl_six_slate_refresh(slug)", leaders)
        self.assertNotIn("auto_update_bowl_six_slates", leaders)

    def test_control_center_and_leaders_api_schedule_refresh(self) -> None:
        site_portal = (
            Path(__file__).resolve().parents[1] / "app" / "routes" / "site_portal.py"
        ).read_text(encoding="utf-8")
        control = site_portal.split("def admin_control_center()", 1)[1].split(
            "def ", 2
        )[0]
        self.assertIn("schedule_bowl_six_slate_refresh(slug)", control)
        self.assertNotIn("auto_update_bowl_six_slates", control)

        api = (
            Path(__file__).resolve().parents[1] / "app" / "routes" / "api.py"
        ).read_text(encoding="utf-8")
        leaders_api = api.split("def bowl_six_leaders_payload()", 1)[1].split(
            "def discord_events_pending()", 1
        )[0]
        self.assertIn("schedule_bowl_six_slate_refresh(slug)", leaders_api)
        self.assertNotIn("auto_update_bowl_six_slates", leaders_api)


if __name__ == "__main__":
    unittest.main()
