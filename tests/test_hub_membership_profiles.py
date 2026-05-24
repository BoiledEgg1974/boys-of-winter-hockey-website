"""Hub membership dashboard GM profile fields."""
from __future__ import annotations

import unittest
from pathlib import Path

from app.site_models import User


class HubMembershipProfilesTest(unittest.TestCase):
    def test_user_model_has_discord_user_id(self) -> None:
        self.assertIn("discord_user_id", User.__table__.columns)

    def test_site_user_migration_adds_discord_user_id(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "db_utils.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn("ALTER TABLE site_users ADD COLUMN discord_user_id VARCHAR(32)", text)

    def test_membership_dashboard_can_edit_discord_user_id(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "hub"
            / "templates"
            / "admin_memberships.html"
        )
        text = path.read_text(encoding="utf-8")
        self.assertIn("Discord User ID", text)
        self.assertIn("admin_update_user_profile", text)
        self.assertIn('name="discord_user_id"', text)

    def test_admin_profile_route_exists(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "hub_auth.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn('"/admin/users/<int:uid>/profile"', text)
        self.assertIn("discord_user_id.isdigit()", text)


if __name__ == "__main__":
    unittest.main()
