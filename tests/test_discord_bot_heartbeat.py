"""Discord bot heartbeat SQLite concurrency."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from sqlalchemy.exc import OperationalError

from app.services.discord_events import prune_obsolete_discord_bot_heartbeats, upsert_bot_heartbeat


class DiscordBotHeartbeatTest(unittest.TestCase):
    def test_prune_skips_when_no_obsolete_rows(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None

        with patch(
            "app.services.discord_events.canonical_discord_bot_name",
            return_value="bowl-news-bot",
        ):
            deleted = prune_obsolete_discord_bot_heartbeats(
                session, league_slug="bowl-cap"
            )

        self.assertEqual(deleted, 0)
        session.execute.assert_not_called()

    def test_prune_uses_sqlite_retry_writer(self) -> None:
        session = MagicMock()
        session.scalar.return_value = 99
        execute_result = MagicMock()
        execute_result.rowcount = 2

        with patch(
            "app.services.discord_events.canonical_discord_bot_name",
            return_value="bowl-news-bot",
        ), patch(
            "app.sqlite_retry.write_with_sqlite_retry",
            return_value=execute_result,
        ) as write_retry:
            deleted = prune_obsolete_discord_bot_heartbeats(
                session, league_slug="bowl-cap"
            )

        self.assertEqual(deleted, 2)
        write_retry.assert_called_once()

    def test_upsert_ignores_prune_lock_errors(self) -> None:
        row = MagicMock(id=1, league_slug="bowl-cap", bot_name="bowl-news-bot")
        session = MagicMock()

        with patch(
            "app.services.discord_events.canonical_discord_bot_name",
            return_value="bowl-news-bot",
        ), patch(
            "app.sqlite_retry.write_with_sqlite_retry",
            return_value=row,
        ), patch(
            "app.services.discord_events.prune_obsolete_discord_bot_heartbeats",
            side_effect=OperationalError("DELETE", {}, Exception("database is locked")),
        ):
            result = upsert_bot_heartbeat(
                session,
                league_slug="bowl-cap",
                bot_name="bowl-news-bot",
                bot_version="1.0.0",
                guild_id="123",
                extra={"pending_count": 0},
            )

        self.assertIs(result, row)
        session.rollback.assert_called_once()


if __name__ == "__main__":
    unittest.main()
