"""Admin GM franchise assignment on the hub membership dashboard."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import delete, func, select
from werkzeug.security import generate_password_hash

from app.league_db import db
from app.services.gm_membership_admin import admin_assign_gm_franchise
from app.site_models import GmLeagueMembership, User
from hub import create_hub_app


class GmMembershipAdminServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = create_hub_app()
        self.app.config["TESTING"] = True
        self.ctx = self.app.app_context()
        self.ctx.push()
        db.create_all()

    def tearDown(self) -> None:
        db.session.execute(delete(GmLeagueMembership))
        db.session.execute(delete(User).where(User.email.like("%@example.com")))
        db.session.commit()
        db.session.remove()
        self.ctx.pop()

    def _add_user(self, email: str | None = None) -> User:
        if email is None:
            email = f"gm-{id(self)}-{db.session.scalar(select(func.count()).select_from(User)) or 0}@example.com"
        user = User(
            email=email,
            password_hash=generate_password_hash("password123"),
            discord_name="Test GM",
        )
        db.session.add(user)
        db.session.flush()
        return user

    @patch("app.services.gm_membership_admin.fhm_team_id_for_league_team", return_value="42")
    @patch("app.services.gm_membership_admin.team_snapshot_for_membership", return_value={"name": "Testers", "abbr": "TST", "fhm_team_id": "42"})
    @patch("app.services.gm_membership_admin.team_id_valid_for_league", return_value=True)
    def test_assign_creates_active_membership(self, *_mocks) -> None:
        user = self._add_user()
        db.session.commit()

        ok, message = admin_assign_gm_franchise(
            db.session,
            user_id=int(user.id),
            league_slug="bowl-cap",
            team_id=7,
        )
        self.assertTrue(ok)
        self.assertIn("Assigned", message)
        db.session.commit()

        row = db.session.scalar(
            select(GmLeagueMembership).where(
                GmLeagueMembership.user_id == int(user.id),
                GmLeagueMembership.league_slug == "bowl-cap",
            )
        )
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.status, "active")
        self.assertEqual(int(row.team_id), 7)
        self.assertEqual(row.fhm_team_id, "42")

    @patch("app.services.gm_membership_admin.fhm_team_id_for_league_team", return_value="99")
    @patch("app.services.gm_membership_admin.team_snapshot_for_membership", return_value={"name": "New Team", "abbr": "NEW", "fhm_team_id": "99"})
    @patch("app.services.gm_membership_admin.team_id_valid_for_league", return_value=True)
    def test_assign_updates_existing_membership_team(self, *_mocks) -> None:
        user = self._add_user()
        db.session.add(
            GmLeagueMembership(
                user_id=int(user.id),
                league_slug="bowl-fantasy",
                team_id=3,
                fhm_team_id="3",
                status="active",
                terms_version="v1",
            )
        )
        db.session.commit()

        ok, message = admin_assign_gm_franchise(
            db.session,
            user_id=int(user.id),
            league_slug="bowl-fantasy",
            team_id=12,
        )
        self.assertTrue(ok)
        self.assertIn("Moved", message)
        db.session.commit()

        row = db.session.scalar(
            select(GmLeagueMembership).where(
                GmLeagueMembership.user_id == int(user.id),
                GmLeagueMembership.league_slug == "bowl-fantasy",
            )
        )
        assert row is not None
        self.assertEqual(int(row.team_id), 12)
        self.assertEqual(row.fhm_team_id, "99")

    @patch("app.services.gm_membership_admin.team_snapshot_for_membership", return_value={"name": "Taken", "abbr": "TKN", "fhm_team_id": "5"})
    @patch("app.services.gm_membership_admin.team_id_valid_for_league", return_value=True)
    def test_assign_blocks_team_conflict_without_replace(self, *_mocks) -> None:
        incumbent = self._add_user()
        newcomer = self._add_user()
        db.session.add(
            GmLeagueMembership(
                user_id=int(incumbent.id),
                league_slug="bowl-historical",
                team_id=5,
                status="active",
                terms_version="v1",
            )
        )
        db.session.commit()

        ok, message = admin_assign_gm_franchise(
            db.session,
            user_id=int(newcomer.id),
            league_slug="bowl-historical",
            team_id=5,
            replace_existing=False,
        )
        self.assertFalse(ok)
        self.assertIn("already has an active GM", message)

    @patch("app.services.gm_membership_admin.fhm_team_id_for_league_team", return_value="5")
    @patch("app.services.gm_membership_admin.team_snapshot_for_membership", return_value={"name": "Taken", "abbr": "TKN", "fhm_team_id": "5"})
    @patch("app.services.gm_membership_admin.team_id_valid_for_league", return_value=True)
    def test_assign_replaces_existing_gm_when_requested(self, *_mocks) -> None:
        incumbent = self._add_user()
        newcomer = self._add_user()
        db.session.add(
            GmLeagueMembership(
                user_id=int(incumbent.id),
                league_slug="bowl-historical",
                team_id=5,
                status="active",
                terms_version="v1",
            )
        )
        db.session.commit()

        ok, _message = admin_assign_gm_franchise(
            db.session,
            user_id=int(newcomer.id),
            league_slug="bowl-historical",
            team_id=5,
            replace_existing=True,
        )
        self.assertTrue(ok)
        db.session.commit()

        old_row = db.session.scalar(
            select(GmLeagueMembership).where(GmLeagueMembership.user_id == int(incumbent.id))
        )
        self.assertIsNone(old_row)
        new_row = db.session.scalar(
            select(GmLeagueMembership).where(GmLeagueMembership.user_id == int(newcomer.id))
        )
        assert new_row is not None
        self.assertEqual(new_row.status, "active")


if __name__ == "__main__":
    unittest.main()
