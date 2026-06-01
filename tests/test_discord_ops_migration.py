"""Discord legacy ops_request_status migration guard."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.services.discord_events import _legacy_ops_request_remains, _migrate_ops_request_to_trade_request


class DiscordOpsMigrationTest(unittest.TestCase):
    def test_skips_migration_when_no_legacy_rows(self) -> None:
        session = MagicMock()
        session.scalar.return_value = None
        self.assertFalse(_legacy_ops_request_remains(session))
        with patch("app.sqlite_retry.commit_with_sqlite_retry") as commit:
            _migrate_ops_request_to_trade_request(session)
            commit.assert_not_called()
            session.execute.assert_not_called()
            session.scalars.assert_not_called()


if __name__ == "__main__":
    unittest.main()
