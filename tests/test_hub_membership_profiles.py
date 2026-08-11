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
        self.assertIn("Site Users / DM Profiles", text)
        self.assertIn("includes site admins", text)
        self.assertIn("admin_update_user_profile", text)
        self.assertIn('name="discord_user_id"', text)

    def test_admin_profile_route_exists(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "hub_auth.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn('"/admin/users/<int:uid>/profile"', text)
        self.assertIn("discord_user_id.isdigit()", text)
        self.assertIn("find_discord_user_id_conflict", text)
        self.assertIn("users=users", text)

    def test_discord_user_id_uniqueness_is_enforced(self) -> None:
        model = Path(__file__).resolve().parents[1] / "app" / "site_models.py"
        self.assertIn("uq_site_users_discord_user_id", model.read_text(encoding="utf-8"))
        db_utils = Path(__file__).resolve().parents[1] / "app" / "db_utils.py"
        self.assertIn("uq_site_users_discord_user_id", db_utils.read_text(encoding="utf-8"))
        dm = (
            Path(__file__).resolve().parents[1]
            / "app"
            / "services"
            / "discord_direct_messages.py"
        )
        self.assertIn("find_discord_user_id_conflict", dm.read_text(encoding="utf-8"))

    def test_membership_dashboard_hides_revoked_deleted_profiles(self) -> None:
        path = Path(__file__).resolve().parents[1] / "app" / "routes" / "hub_auth.py"
        text = path.read_text(encoding="utf-8")
        self.assertIn(".where(User.revoked_at.is_(None))", text)
        self.assertIn("deleted-user-", text)

    def test_membership_dashboard_has_admin_assign_franchise(self) -> None:
        hub_auth = Path(__file__).resolve().parents[1] / "app" / "routes" / "hub_auth.py"
        self.assertIn('"/admin/memberships/assign"', hub_auth.read_text(encoding="utf-8"))
        template = (
            Path(__file__).resolve().parents[1]
            / "hub"
            / "templates"
            / "admin_memberships.html"
        )
        text = template.read_text(encoding="utf-8")
        self.assertIn("Assign / change franchise", text)
        self.assertIn("admin_assign_membership", text)
        self.assertIn("Change team", text)


if __name__ == "__main__":
    unittest.main()
