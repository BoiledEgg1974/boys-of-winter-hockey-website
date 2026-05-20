"""Commissioner admin self-heal on user load."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.auth_login import ensure_commissioner_admin_flags, has_admin_role, is_commissioner_identity


class CommissionerAdminTest(unittest.TestCase):
    def test_commissioner_username_detected(self):
        user = MagicMock(email="other@example.com", username="Commish", is_admin=False, admin_role=None)
        self.assertTrue(is_commissioner_identity(user))

    def test_ensure_flags_sets_super_admin(self):
        user = MagicMock(
            email="keenovdecimanus@gmail.com",
            username="Commish",
            is_admin=False,
            admin_role=None,
        )
        with patch("app.auth_login.db") as mock_db:
            changed = ensure_commissioner_admin_flags(user)
        self.assertTrue(changed)
        self.assertTrue(user.is_admin)
        self.assertEqual(user.admin_role, "super_admin")
        mock_db.session.commit.assert_called_once()

    def test_has_admin_role_after_flags(self):
        user = MagicMock(
            email="keenovdecimanus@gmail.com",
            username="Commish",
            is_admin=True,
            admin_role="super_admin",
            is_authenticated=True,
        )
        self.assertTrue(has_admin_role(user))


if __name__ == "__main__":
    unittest.main()
