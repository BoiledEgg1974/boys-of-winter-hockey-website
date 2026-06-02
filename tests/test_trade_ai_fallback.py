"""AI trade tool provider-failure fallback behavior."""
from __future__ import annotations

import unittest
from io import BytesIO
from urllib.error import HTTPError
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services import trade_ai_opinion
from app.services.trade_ai_opinion import fetch_trade_ai_opinion


class TradeAiFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        trade_ai_opinion._LAST_CALL_BY_USER.clear()

    def test_missing_key_returns_local_fallback_not_error(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["TRADE_AI_OPENAI_API_KEY"] = ""
        with app.app_context():
            out = fetch_trade_ai_opinion(
                MagicMock(),
                user_id=991,
                from_team=MagicMock(full_display_name=lambda: "Hamilton"),
                to_team=MagicMock(full_display_name=lambda: "Toronto"),
                left=["manual_pick:1:x"],
                right=["manual_pick:2:y"],
                notes="",
                league_slug="bowl-cap",
            )
        self.assertNotIn("error", out)
        self.assertTrue(out.get("fallback"))
        self.assertIn("Local scout take", out.get("opinion", ""))

    def test_openai_401_returns_fallback_without_provider_details(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        app.config["TRADE_AI_OPENAI_API_KEY"] = "sk-proj-bad"
        body = b'{"error":{"message":"Incorrect API key provided: sk-proj-secret"}}'
        err = HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs={},
            fp=BytesIO(body),
        )
        with app.app_context(), patch("app.services.trade_ai_opinion.urlopen", side_effect=err):
            out = fetch_trade_ai_opinion(
                MagicMock(),
                user_id=992,
                from_team=MagicMock(full_display_name=lambda: "Hamilton"),
                to_team=MagicMock(full_display_name=lambda: "Toronto"),
                left=["manual_pick:1:x"],
                right=["manual_pick:2:y"],
                notes="",
                league_slug="bowl-cap",
            )
        self.assertNotIn("error", out)
        self.assertTrue(out.get("fallback"))
        self.assertNotIn("Incorrect API key", str(out))
        self.assertNotIn("sk-proj-secret", str(out))


if __name__ == "__main__":
    unittest.main()
