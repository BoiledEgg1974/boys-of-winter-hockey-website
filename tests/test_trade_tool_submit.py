"""Admin-only Trade Tool: publish on submit across all league mounts."""
from __future__ import annotations

import json
import re
import unittest
from sqlalchemy import delete, select

from app import create_app
from app.config import LEAGUES, make_league_config
from app.league_db import db
from app.models import Player, Team
from app.services.trade_tool import STATUS_PUBLISHED
from app.site_models import (
    GmInAppNotification,
    GmLeagueMembership,
    GmLeagueMessage,
    GmTradeProposal,
    NewsArticle,
    User,
)

_EMAIL_GM = "trade-test-gm@example.invalid"
_EMAIL_ADMIN = "trade-test-admin@example.invalid"


def _cleanup_test_users() -> None:
    for email in (_EMAIL_GM, _EMAIL_ADMIN):
        user = db.session.scalar(select(User).where(User.email == email))
        if not user:
            continue
        uid = int(user.id)
        db.session.execute(
            delete(GmTradeProposal).where(
                (GmTradeProposal.from_user_id == uid)
                | (GmTradeProposal.to_user_id == uid)
                | (GmTradeProposal.commissioner_user_id == uid)
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
    db.session.commit()


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    return match.group(1) if match else ""


def _login(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user_id)
        sess["_fresh"] = True


class TradeToolAdminOnlyTest(unittest.TestCase):
    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        with app.app_context():
            _cleanup_test_users()
            db.session.remove()

    def _teams_and_player(self) -> tuple[Team, Team, Player]:
        teams = list(db.session.scalars(select(Team).order_by(Team.id).limit(2)).all())
        self.assertGreaterEqual(len(teams), 2)
        team_a, team_b = teams[0], teams[1]
        player = db.session.scalar(
            select(Player).where(
                Player.current_team_id == int(team_a.id),
                Player.retired.is_(False),
            ).limit(1)
        )
        self.assertIsNotNone(player)
        return team_a, team_b, player

    def _setup_admin(self, slug: str) -> tuple[User, User, Team, Team, Player]:
        _cleanup_test_users()
        team_a, team_b, player = self._teams_and_player()
        gm = User(email=_EMAIL_GM, password_hash="x", discord_name="GM Test")
        admin = User(
            email=_EMAIL_ADMIN,
            password_hash="x",
            discord_name="League Admin",
            is_admin=True,
            admin_role="league_admin",
        )
        db.session.add_all([gm, admin])
        db.session.flush()
        db.session.add(
            GmLeagueMembership(
                league_slug=slug,
                user_id=int(gm.id),
                team_id=int(team_a.id),
                status="active",
            )
        )
        db.session.commit()
        return admin, gm, team_a, team_b, player

    def test_gm_cannot_access_trade_tool_all_leagues(self) -> None:
        for entry in LEAGUES:
            slug = entry.slug
            self.app = create_app(make_league_config(slug))
            with self.app.app_context():
                _admin, gm, _ta, _tb, _pl = self._setup_admin(slug)
                with self.app.test_client() as client:
                    _login(client, int(gm.id))
                    resp = client.get("/trade-tool", follow_redirects=False)
                    self.assertEqual(resp.status_code, 302, slug)
                    self.assertIn("/", resp.location or "")

    def test_admin_publish_trade_all_leagues(self) -> None:
        for entry in LEAGUES:
            slug = entry.slug
            self.app = create_app(make_league_config(slug))
            with self.app.app_context():
                admin, gm, team_a, team_b, player = self._setup_admin(slug)
                ledger = json.dumps(
                    {
                        "from_left_to_right": [f"player:{int(player.id)}"],
                        "from_right_to_left": [],
                    }
                )
                with self.app.test_client() as client:
                    _login(client, int(admin.id))
                    tool_page = client.get(
                        f"/trade-tool?admin_team_id={int(team_a.id)}",
                    )
                    self.assertEqual(tool_page.status_code, 200, slug)
                    token = _csrf_token(tool_page.get_data(as_text=True))
                    self.assertTrue(token, f"{slug}: CSRF token")

                    submit = client.post(
                        "/operations/trade-tool/submit",
                        data={
                            "csrf_token": token,
                            "admin_team_id": str(int(team_a.id)),
                            "partner_team_id": str(int(team_b.id)),
                            "ledger_json": ledger,
                            "notes": "admin unit test trade",
                        },
                        follow_redirects=False,
                    )
                    self.assertEqual(submit.status_code, 302, slug)

                    proposal = db.session.scalar(
                        select(GmTradeProposal)
                        .where(
                            GmTradeProposal.league_slug == slug,
                            GmTradeProposal.commissioner_user_id == int(admin.id),
                        )
                        .order_by(GmTradeProposal.id.desc())
                    )
                    self.assertIsNotNone(proposal, f"{slug}: proposal not saved")
                    self.assertEqual(proposal.status, STATUS_PUBLISHED)
                    self.assertEqual(int(proposal.from_team_id), int(team_a.id))
                    self.assertEqual(int(proposal.to_team_id), int(team_b.id))
                    self.assertEqual(int(proposal.from_user_id), int(gm.id))

                    db.session.refresh(player)
                    self.assertEqual(
                        int(player.current_team_id),
                        int(team_a.id),
                        f"{slug}: trade publish must not move players in league DB (CSV import updates rosters)",
                    )

                    articles = list(
                        db.session.scalars(
                            select(NewsArticle).where(
                                NewsArticle.league_slug == slug,
                                NewsArticle.category == "transactions",
                                NewsArticle.author_user_id == int(admin.id),
                            )
                        ).all()
                    )
                    self.assertGreaterEqual(len(articles), 2, f"{slug}: transaction articles")

    def test_gm_can_load_ai_trade_assets(self) -> None:
        entry = LEAGUES[0]
        slug = entry.slug
        self.app = create_app(make_league_config(slug))
        with self.app.app_context():
            _admin, gm, team_a, team_b, _player = self._setup_admin(slug)
            with self.app.test_client() as client:
                _login(client, int(gm.id))
                resp = client.get(
                    f"/operations/trade-tool/assets?ai=1&partner_team_id={int(team_b.id)}"
                )
                self.assertEqual(resp.status_code, 200, slug)
                data = resp.get_json()
                self.assertIsNotNone(data)
                self.assertEqual(int(data["left_team_id"]), int(team_a.id))
                self.assertEqual(int(data["right_team_id"]), int(team_b.id))

    def test_trade_submit_uses_sqlite_retry(self) -> None:
        from pathlib import Path

        text = (
            Path(__file__).resolve().parents[1] / "app" / "routes" / "site_portal.py"
        ).read_text(encoding="utf-8")
        self.assertIn("def _persist_and_publish_trade", text)
        self.assertIn("write_with_sqlite_retry(db.session, _persist_and_publish_trade)", text)
        self.assertIn("publish_trade_proposal(", text)


if __name__ == "__main__":
    unittest.main()
