"""Trade Tool submit + partner approval across all league mounts."""
from __future__ import annotations

import json
import re
import unittest
from sqlalchemy import delete, select

from app import create_app
from app.config import LEAGUES, make_league_config
from app.league_db import db
from app.models import Player, Team
from app.services.trade_tool import STATUS_PENDING_PARTNER
from app.site_models import (
    GmInAppNotification,
    GmLeagueMembership,
    GmLeagueMessage,
    GmTradeProposal,
    User,
)

_EMAIL_A = "trade-test-a@example.invalid"
_EMAIL_B = "trade-test-b@example.invalid"


def _suspend_active_team_memberships(slug: str, team_ids: list[int]) -> list[tuple[int, str]]:
    """Temporarily deactivate real GMs on test teams so partner lookup is deterministic."""
    if not team_ids:
        return []
    rows = list(
        db.session.scalars(
            select(GmLeagueMembership).where(
                GmLeagueMembership.league_slug == slug,
                GmLeagueMembership.team_id.in_([int(tid) for tid in team_ids]),
                GmLeagueMembership.status == "active",
            )
        ).all()
    )
    saved: list[tuple[int, str]] = []
    for row in rows:
        saved.append((int(row.id), str(row.status or "active")))
        row.status = "inactive"
    if saved:
        db.session.flush()
    return saved


def _restore_membership_statuses(saved: list[tuple[int, str]]) -> None:
    for membership_id, status in saved:
        row = db.session.get(GmLeagueMembership, int(membership_id))
        if row is not None:
            row.status = status


def _cleanup_test_users(restored_memberships: list[tuple[int, str]] | None = None) -> None:
    for email in (_EMAIL_A, _EMAIL_B):
        user = db.session.scalar(select(User).where(User.email == email))
        if not user:
            continue
        uid = int(user.id)
        db.session.execute(
            delete(GmTradeProposal).where(
                (GmTradeProposal.from_user_id == uid) | (GmTradeProposal.to_user_id == uid)
            )
        )
        db.session.execute(delete(GmInAppNotification).where(GmInAppNotification.user_id == uid))
        db.session.execute(
            delete(GmLeagueMessage).where(
                (GmLeagueMessage.from_user_id == uid) | (GmLeagueMessage.to_user_id == uid)
            )
        )
        db.session.execute(delete(GmLeagueMembership).where(GmLeagueMembership.user_id == uid))
        db.session.delete(user)
    if restored_memberships:
        _restore_membership_statuses(restored_memberships)
    db.session.commit()


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else ""


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


class TradeToolSubmitTest(unittest.TestCase):
    def setUp(self) -> None:
        self._restored_memberships: list[tuple[int, str]] = []

    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        with app.app_context():
            _cleanup_test_users(self._restored_memberships)
            self._restored_memberships = []
            db.session.remove()

    def _setup_users_for_league(self, slug: str) -> tuple[User, User, Team, Team, Player]:
        teams = list(db.session.scalars(select(Team).order_by(Team.id).limit(2)).all())
        self.assertGreaterEqual(len(teams), 2, f"{slug}: need at least two teams")
        team_a, team_b = teams[0], teams[1]
        player = db.session.scalar(
            select(Player).where(
                Player.current_team_id == int(team_a.id),
                Player.retired.is_(False),
            ).limit(1)
        )
        self.assertIsNotNone(player, f"{slug}: need a roster player on team {team_a.id}")

        _cleanup_test_users()
        self._restored_memberships.extend(
            _suspend_active_team_memberships(slug, [int(team_a.id), int(team_b.id)])
        )
        user_a = User(email=_EMAIL_A, password_hash="x", discord_name="GM A")
        user_b = User(email=_EMAIL_B, password_hash="x", discord_name="GM B")
        db.session.add_all([user_a, user_b])
        db.session.flush()
        db.session.add_all(
            [
                GmLeagueMembership(
                    league_slug=slug,
                    user_id=int(user_a.id),
                    team_id=int(team_a.id),
                    status="active",
                ),
                GmLeagueMembership(
                    league_slug=slug,
                    user_id=int(user_b.id),
                    team_id=int(team_b.id),
                    status="active",
                ),
            ]
        )
        db.session.commit()
        return user_a, user_b, team_a, team_b, player

    def test_trade_submit_all_leagues(self) -> None:
        for entry in LEAGUES:
            slug = entry.slug
            self.app = create_app(make_league_config(slug))
            with self.app.app_context():
                user_a, user_b, team_a, team_b, player = self._setup_users_for_league(slug)
                ledger = json.dumps(
                    {
                        "from_left_to_right": [f"player:{int(player.id)}"],
                        "from_right_to_left": [],
                    }
                )
                with self.app.test_client() as client:
                    _login(client, int(user_a.id))
                    tool_page = client.get("/trade-tool")
                    self.assertEqual(tool_page.status_code, 200, slug)
                    token = _csrf_token(tool_page.get_data(as_text=True))
                    self.assertTrue(token, f"{slug}: CSRF token on trade tool page")

                    submit = client.post(
                        "/operations/trade-tool/submit",
                        data={
                            "csrf_token": token,
                            "partner_team_id": str(int(team_b.id)),
                            "ledger_json": ledger,
                            "notes": "unit test trade",
                        },
                        follow_redirects=False,
                    )
                    self.assertEqual(
                        submit.status_code,
                        302,
                        f"{slug}: submit failed — {submit.get_data(as_text=True)[:400]}",
                    )

                    proposal = db.session.scalar(
                        select(GmTradeProposal)
                        .where(
                            GmTradeProposal.league_slug == slug,
                            GmTradeProposal.from_user_id == int(user_a.id),
                        )
                        .order_by(GmTradeProposal.id.desc())
                    )
                    self.assertIsNotNone(proposal, f"{slug}: proposal not saved")
                    self.assertEqual(proposal.status, STATUS_PENDING_PARTNER)
                    self.assertEqual(int(proposal.to_user_id), int(user_b.id))
                    self.assertEqual(int(proposal.to_team_id), int(team_b.id))

    def test_trade_submit_uses_sqlite_retry(self) -> None:
        from pathlib import Path

        text = (
            Path(__file__).resolve().parents[1] / "app" / "routes" / "site_portal.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def _persist_trade_submission", text)
        self.assertIn("write_with_sqlite_retry(db.session, _persist_trade_submission)", text)
        self.assertIn("write_with_sqlite_retry(db.session, _approve_trade)", text)


if __name__ == "__main__":
    unittest.main()
