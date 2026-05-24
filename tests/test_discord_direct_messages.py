from __future__ import annotations

import json
import unittest

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.services.discord_direct_messages import (
    enqueue_direct_message,
    fetch_pending_direct_messages_for_bot,
    mark_direct_message_failed,
    mark_direct_message_sent,
    serialize_direct_messages_for_bot,
)
from app.services.discord_interactions import handle_slash_interaction
from app.site_models import DiscordDirectMessageEvent, User
from scripts.league_discord_bot.formatters import format_direct_message


class DiscordDirectMessageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_app(make_league_config("bowl-fantasy"))
        self.ctx = self.app.app_context()
        self.ctx.push()
        self.email = "dm-test@example.invalid"
        old = db.session.query(User).filter(User.email == self.email).all()
        for u in old:
            db.session.delete(u)
        db.session.query(DiscordDirectMessageEvent).filter(
            DiscordDirectMessageEvent.league_slug == "bowl-fantasy",
            DiscordDirectMessageEvent.event_key.like("test_%"),
        ).delete(synchronize_session=False)
        db.session.commit()
        self.user = User(
            email=self.email,
            username=None,
            password_hash="x",
            discord_name="Coach Test",
            discord_user_id="123456789012345678",
            discord_dm_enabled=True,
        )
        db.session.add(self.user)
        db.session.commit()

    def tearDown(self) -> None:
        db.session.query(DiscordDirectMessageEvent).filter(
            DiscordDirectMessageEvent.recipient_user_id == self.user.id
        ).delete(synchronize_session=False)
        db.session.delete(self.user)
        db.session.commit()
        db.session.remove()
        self.ctx.pop()

    def test_enqueue_direct_message_is_idempotent_and_serializes(self) -> None:
        first = enqueue_direct_message(
            db.session,
            league_slug="bowl-fantasy",
            recipient_user_id=self.user.id,
            event_key="test_message",
            title="Inbox alert",
            body="A short preview is available.",
            source_type="unit",
            source_id="1",
            url="https://example.test/bowl-fantasy/gm/messages",
        )
        second = enqueue_direct_message(
            db.session,
            league_slug="bowl-fantasy",
            recipient_user_id=self.user.id,
            event_key="test_message",
            title="Inbox alert",
            body="A short preview is available.",
            source_type="unit",
            source_id="1",
            url="https://example.test/bowl-fantasy/gm/messages",
        )
        db.session.commit()
        self.assertIsNotNone(first)
        self.assertEqual(first.id, second.id)
        rows = fetch_pending_direct_messages_for_bot(db.session, league_slug="bowl-fantasy")
        payloads = serialize_direct_messages_for_bot([r for r in rows if r.id == first.id])
        self.assertEqual(payloads[0]["discord_user_id"], "123456789012345678")
        self.assertEqual(payloads[0]["payload"]["discord_name"], "Coach Test")

    def test_ack_and_fail_update_status(self) -> None:
        row = enqueue_direct_message(
            db.session,
            league_slug="bowl-fantasy",
            recipient_user_id=self.user.id,
            event_key="test_ack",
            title="Ack alert",
            source_type="unit",
            source_id="2",
        )
        db.session.commit()
        self.assertTrue(mark_direct_message_failed(db.session, row.id, "temporary"))
        db.session.refresh(row)
        self.assertEqual(row.status, "pending")
        self.assertEqual(row.attempts, 1)
        self.assertTrue(
            mark_direct_message_sent(
                db.session,
                row.id,
                discord_channel_id="234567890123456789",
                discord_message_id="345678901234567890",
            )
        )
        db.session.refresh(row)
        self.assertEqual(row.status, "sent")
        self.assertEqual(row.discord_message_id, "345678901234567890")

    def test_format_direct_message_uses_assistant_voice(self) -> None:
        body = format_direct_message(
            {
                "league_slug": "bowl-fantasy",
                "payload": {
                    "discord_name": "Coach Test",
                    "title": "Trade proposal pending",
                    "preview": "Open GM Messages to review.",
                    "url": "https://example.test/gm/messages",
                },
            }
        )
        content = body["content"]
        self.assertIn("your BOWL assistant here", content)
        self.assertIn("Coach Test", content)
        self.assertIn("Trade proposal pending", content)

    def test_inbox_slash_command_matches_discord_user(self) -> None:
        response = handle_slash_interaction(
            {
                "type": 2,
                "data": {"name": "inbox"},
                "member": {"user": {"id": "123456789012345678"}},
            }
        )
        self.assertEqual(response["type"], 4)
        self.assertEqual(response["data"]["flags"], 64)
        self.assertIn("caught up", response["data"]["content"])


if __name__ == "__main__":
    unittest.main()
