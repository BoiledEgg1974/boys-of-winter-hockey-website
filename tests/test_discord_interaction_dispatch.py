from __future__ import annotations

import json
import time
import unittest

from nacl.signing import SigningKey

from app.config import make_league_config
from app.league_db import db
from app.services.discord_events import get_league_bot_config, update_league_bot_config
from app.services.discord_interaction_dispatch import (
    clear_league_app_cache,
    guild_id_from_interaction,
    league_slug_for_guild_id,
    process_discord_interaction,
)
from app.site_models import DiscordLeagueBotConfig, User
from hub import create_hub_app


class DiscordInteractionDispatchTests(unittest.TestCase):
  def setUp(self) -> None:
        self.hub = create_hub_app()
        self.hub_ctx = self.hub.app_context()
        self.hub_ctx.push()
        self.signing_key = SigningKey.generate()
        self.public_key = self.signing_key.verify_key.encode().hex()
        self.guild_fantasy = "900000000000000001"
        self.guild_historical = "900000000000000002"
        for slug, gid in (
            ("bowl-fantasy", self.guild_fantasy),
            ("bowl-historical", self.guild_historical),
        ):
            update_league_bot_config(
                db.session,
                league_slug=slug,
                guild_id=gid,
                is_enabled=True,
                notes="",
                updated_by_user_id=1,
            )
        db.session.commit()
        clear_league_app_cache()

        from app import create_app

        self.fantasy_app = create_app(make_league_config("bowl-fantasy"))
        self.fantasy_ctx = self.fantasy_app.app_context()
        self.fantasy_ctx.push()
        self.email = "dispatch-test@example.invalid"
        for u in db.session.query(User).filter(User.email == self.email).all():
            db.session.delete(u)
        self.user = User(
            email=self.email,
            username=None,
            password_hash="x",
            discord_name="Dispatch Test",
            discord_user_id="987654321098765432",
            discord_dm_enabled=True,
        )
        db.session.add(self.user)
        db.session.commit()

  def tearDown(self) -> None:
        db.session.query(DiscordLeagueBotConfig).filter(
            DiscordLeagueBotConfig.guild_id.in_([self.guild_fantasy, self.guild_historical])
        ).update({DiscordLeagueBotConfig.guild_id: ""}, synchronize_session=False)
        db.session.delete(self.user)
        db.session.commit()
        db.session.remove()
        self.fantasy_ctx.pop()
        self.hub_ctx.pop()
        clear_league_app_cache()

  def _signed_post(self, payload: dict) -> tuple[int, dict]:
        body = json.dumps(payload, separators=(",", ":")).encode()
        ts = str(int(time.time()))
        sig = self.signing_key.sign(ts.encode() + body).signature.hex()
        return process_discord_interaction(
            raw_body=body,
            timestamp=ts,
            signature=sig,
            public_key=self.public_key,
            shared_secret="",
        )

  def test_guild_id_from_interaction(self) -> None:
        self.assertEqual(
            guild_id_from_interaction({"guild_id": "123", "member": {}}),
            "123",
        )
        self.assertEqual(
            guild_id_from_interaction({"member": {"guild_id": "456"}}),
            "456",
        )

  def test_league_slug_for_guild_id(self) -> None:
        self.assertEqual(league_slug_for_guild_id(self.guild_fantasy), "bowl-fantasy")
        self.assertEqual(league_slug_for_guild_id(self.guild_historical), "bowl-historical")
        self.assertIsNone(league_slug_for_guild_id("000000000000000000"))

  def test_ping_returns_pong_without_guild(self) -> None:
        status, body = self._signed_post({"type": 1})
        self.assertEqual(status, 200)
        self.assertEqual(body, {"type": 1})

  def test_unknown_guild_returns_ephemeral_help(self) -> None:
        status, body = self._signed_post(
            {
                "type": 2,
                "guild_id": "000000000000000099",
                "data": {"name": "standings"},
                "member": {"user": {"id": "1"}},
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["type"], 4)
        self.assertIn("not linked", body["data"]["content"])

  def test_routes_fantasy_guild_to_fantasy_league(self) -> None:
        status, body = self._signed_post(
            {
                "type": 2,
                "guild_id": self.guild_fantasy,
                "data": {"name": "inbox"},
                "member": {"user": {"id": "987654321098765432"}},
            }
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["type"], 4)
        content = body["data"]["content"]
        self.assertNotIn("not linked", content)
        self.assertTrue("caught up" in content or "bowl-fantasy" in content)

  def test_invalid_signature_is_rejected(self) -> None:
        status, body = process_discord_interaction(
            raw_body=b'{"type":1}',
            timestamp=str(int(time.time())),
            signature="00" * 64,
            public_key=self.public_key,
            shared_secret="",
        )
        self.assertEqual(status, 401)
        self.assertIn("signature", body["error"])


if __name__ == "__main__":
    unittest.main()
