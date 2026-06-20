"""Discord outbound ack commits retry on SQLite lock errors."""
from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

from app.services.discord_events import mark_event_failed, mark_event_sent


class DiscordEventAckRetryTests(unittest.TestCase):
    def test_mark_event_sent_uses_sqlite_retry_commit(self) -> None:
        session = MagicMock()
        row = MagicMock()
        row.status = "pending"
        row.event_key = "news_article"
        row.league_slug = "bowl-cap"
        row.id = 7
        row.attempts = 0
        session.get.return_value = row

        with patch("app.services.discord_events._parse_payload", return_value={}), patch(
            "app.sqlite_retry.commit_with_sqlite_retry"
        ) as commit_retry:
            ok = mark_event_sent(session, 7, discord_message_id="123")

        self.assertTrue(ok)
        commit_retry.assert_called_once_with(session)

    def test_mark_bowl_six_sent_requires_message_id(self) -> None:
        session = MagicMock()
        row = MagicMock()
        row.status = "pending"
        row.event_key = "bowl_six_leaders_update"
        row.league_slug = "bowl-historical"
        row.id = 8
        session.get.return_value = row

        with patch("app.services.discord_events._parse_payload", return_value={"slate_id": 1}):
            ok = mark_event_sent(session, 8, discord_message_id="")

        self.assertFalse(ok)
        self.assertEqual(row.status, "pending")

    def test_mark_event_failed_uses_sqlite_retry_commit(self) -> None:
        session = MagicMock()
        row = MagicMock()
        row.status = "pending"
        row.attempts = 1
        session.get.return_value = row

        with patch("app.sqlite_retry.commit_with_sqlite_retry") as commit_retry:
            ok = mark_event_failed(session, 9, error="channel missing")

        self.assertTrue(ok)
        commit_retry.assert_called_once_with(session)


if __name__ == "__main__":
    unittest.main()
