"""Line Builder role scores, org-player guard, and save auth."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from sqlalchemy import delete, select

from app import create_app
from app.config import make_league_config
from app.league_db import db
from app.models import Player, Team
from app.services.player_line_roles import (
    default_role_key,
    line_ability,
    line_ability_grade,
    line_chemistry,
    player_role_group,
    role_scores_for_player,
    score_role,
)
from app.services.team_line_sheet import can_edit_line_sheet, sanitize_roles, sanitize_slots
from app.site_models import GmLeagueMembership, TeamLineSheet, User

_EMAIL_GM = "line-builder-gm@example.invalid"
_EMAIL_OTHER = "line-builder-other@example.invalid"
_EMAIL_ADMIN = "line-builder-admin@example.invalid"


class RoleScoringTests(unittest.TestCase):
    def test_score_role_scales_0_20_to_1_100(self) -> None:
        weights = (("shooting", 1.0),)
        self.assertEqual(score_role({"shooting": 20}, weights), 100)
        self.assertEqual(score_role({"shooting": 0}, weights), 1)
        self.assertEqual(score_role({"shooting": 10}, weights), 50)
        self.assertIsNone(score_role({}, weights))

    def test_default_role_is_highest(self) -> None:
        scores = role_scores_for_player(
            {
                "shooting_accuracy": 20,
                "shooting_range": 20,
                "getting_open": 20,
                "offensive_read": 18,
                "shooting": 19,
                "passing": 8,
                "puck_handling": 8,
                "playmaking": 8,
                "hockey_sense": 8,
            },
            position="RW",
        )
        self.assertTrue(scores)
        self.assertEqual(scores[0]["key"], default_role_key(scores))
        self.assertGreaterEqual(scores[0]["rating"], scores[-1]["rating"])
        self.assertTrue(all(1 <= int(r["rating"]) <= 100 for r in scores))

    def test_defense_and_goalie_groups(self) -> None:
        self.assertEqual(player_role_group("LD"), "defense")
        self.assertEqual(player_role_group("G"), "goalies")
        self.assertEqual(player_role_group("C"), "forwards")

    def test_line_ability_and_chemistry(self) -> None:
        self.assertEqual(line_ability([80, 90, None]), 85.0)
        self.assertIsNone(line_ability([None, None]))
        chem = line_chemistry(["sniper", "playmaker"], ["L", "R"])
        self.assertIsNotNone(chem)
        self.assertGreater(chem, line_chemistry(["sniper", "sniper"], ["L", "L"]))

    def test_line_ability_grade_tiers(self) -> None:
        self.assertEqual(line_ability_grade(86)["label"], "1st line")
        self.assertEqual(line_ability_grade(80)["label"], "2nd line")
        self.assertEqual(line_ability_grade(70)["label"], "3rd line")
        self.assertEqual(line_ability_grade(62)["label"], "4th line")
        self.assertEqual(line_ability_grade(50)["key"], "depth")
        self.assertEqual(line_ability_grade(50)["label"], "Depth")
        self.assertEqual(line_ability_grade(86, kind="defense")["label"], "1st Pair")
        self.assertEqual(line_ability_grade(80, kind="defense")["label"], "2nd Pair")
        self.assertEqual(line_ability_grade(50, kind="defense")["label"], "Depth pair")
        self.assertIsNone(line_ability_grade(None))


class SanitizeAndAuthTests(unittest.TestCase):
    def test_org_guard_rejects_unknown_player_and_slot(self) -> None:
        slots, err = sanitize_slots({"es_l1_c": 99}, {1})
        self.assertEqual(slots, {})
        self.assertEqual(err, "player is not on this organization")
        slots, err = sanitize_slots({"shootout_1": 1}, {1})
        self.assertIn("unknown slot", err or "")
        slots, err = sanitize_slots({"es_l1_c": 1, "es_l1_lw": 1}, {1})
        self.assertIn("two slots", err or "")
        slots, err = sanitize_slots({"es_l1_c": 1, "pp_l1_c": 1}, {1})
        self.assertEqual(slots, {"es_l1_c": 1, "pp_l1_c": 1})
        self.assertIsNone(err)
        slots, err = sanitize_slots({"es_l1_c": 1}, {1})
        self.assertEqual(slots, {"es_l1_c": 1})
        self.assertIsNone(err)

    def test_roles_guard(self) -> None:
        roles, err = sanitize_roles({"1": "sniper"}, {1})
        self.assertEqual(roles, {"1": "sniper"})
        self.assertIsNone(err)
        roles, err = sanitize_roles({"9": "sniper"}, {1})
        self.assertEqual(err, "player is not on this organization")
        roles, err = sanitize_roles({"1": "not_a_role"}, {1})
        self.assertIn("unknown role", err or "")

    def test_can_edit_gm_or_admin_only(self) -> None:
        session = MagicMock()
        anon = SimpleNamespace(is_authenticated=False, id=1)
        self.assertFalse(can_edit_line_sheet(anon, "bowl-cap", 8, session))
        gm = SimpleNamespace(is_authenticated=True, id=11, admin_role=None, is_admin=False)
        with patch("app.services.team_line_sheet.has_admin_role", return_value=False), patch(
            "app.services.team_line_sheet.gm_user_id_for_team", return_value=11
        ):
            self.assertTrue(can_edit_line_sheet(gm, "bowl-cap", 8, session))
        with patch("app.services.team_line_sheet.has_admin_role", return_value=False), patch(
            "app.services.team_line_sheet.gm_user_id_for_team", return_value=99
        ):
            self.assertFalse(can_edit_line_sheet(gm, "bowl-cap", 8, session))
        admin = SimpleNamespace(is_authenticated=True, id=22)
        with patch("app.services.team_line_sheet.has_admin_role", return_value=True):
            self.assertTrue(can_edit_line_sheet(admin, "bowl-cap", 8, session))


class LineBuilderApiTests(unittest.TestCase):
    def tearDown(self) -> None:
        app = getattr(self, "app", None)
        if app is None:
            return
        with app.app_context():
            for email in (_EMAIL_GM, _EMAIL_OTHER, _EMAIL_ADMIN):
                user = db.session.scalar(select(User).where(User.email == email))
                if not user:
                    continue
                db.session.execute(delete(TeamLineSheet).where(TeamLineSheet.updated_by_user_id == user.id))
                db.session.execute(delete(GmLeagueMembership).where(GmLeagueMembership.user_id == user.id))
                db.session.delete(user)
            db.session.commit()
            db.session.remove()

    def _login(self, client, user_id: int) -> None:
        with client.session_transaction() as sess:
            sess["_user_id"] = str(user_id)
            sess["_fresh"] = True

    @staticmethod
    def _slot_for(pos: str) -> str:
        p = (pos or "").strip().upper()
        if p in {"D", "LD", "RD"}:
            return "es_l1_ld"
        return "es_l1_c"

    def _setup(self, slug: str = "bowl-cap"):
        self.app = create_app(make_league_config(slug))
        with self.app.app_context():
            for email in (_EMAIL_GM, _EMAIL_OTHER, _EMAIL_ADMIN):
                user = db.session.scalar(select(User).where(User.email == email))
                if user:
                    db.session.execute(delete(TeamLineSheet).where(TeamLineSheet.updated_by_user_id == user.id))
                    db.session.execute(delete(GmLeagueMembership).where(GmLeagueMembership.user_id == user.id))
                    db.session.delete(user)
            db.session.commit()
            team = db.session.scalar(select(Team).where(Team.slug == "chi-t8").limit(1))
            if team is None:
                team = db.session.scalar(select(Team).order_by(Team.id).limit(1))
            self.assertIsNotNone(team)
            player = db.session.scalar(
                select(Player).where(
                    Player.current_team_id == int(team.id),
                    Player.retired.is_(False),
                    Player.position.in_(("C", "LW", "RW")),
                ).limit(1)
            )
            if player is None:
                player = db.session.scalar(
                    select(Player).where(Player.current_team_id == int(team.id), Player.retired.is_(False)).limit(1)
                )
            self.assertIsNotNone(player)
            other_player = db.session.scalar(
                select(Player).where(
                    Player.current_team_id.isnot(None),
                    Player.current_team_id != int(team.id),
                    Player.retired.is_(False),
                ).limit(1)
            )
            gm = User(email=_EMAIL_GM, password_hash="x", discord_name="LB GM")
            other = User(email=_EMAIL_OTHER, password_hash="x", discord_name="LB Other")
            admin = User(
                email=_EMAIL_ADMIN,
                password_hash="x",
                discord_name="LB Admin",
                is_admin=True,
                admin_role="league_admin",
            )
            db.session.add_all([gm, other, admin])
            db.session.flush()
            db.session.add(
                GmLeagueMembership(
                    league_slug=slug,
                    user_id=int(gm.id),
                    team_id=int(team.id),
                    status="active",
                )
            )
            ctx = {
                "slug": slug,
                "team_slug": team.slug,
                "team_id": int(team.id),
                "player_id": int(player.id),
                "player_pos": (player.position or "").strip().upper(),
                "other_id": int(other_player.id) if other_player is not None else None,
                "gm_id": int(gm.id),
                "other_user_id": int(other.id),
                "admin_id": int(admin.id),
            }
            db.session.commit()
            return ctx

    def test_visitor_cannot_save(self) -> None:
        ctx = self._setup()
        slot = self._slot_for(ctx["player_pos"])
        with self.app.test_client() as client:
            resp = client.post(
                f"/api/team/{ctx['team_slug']}/line-sheet",
                json={"slots": {slot: ctx["player_id"]}, "roles": {}},
            )
            self.assertEqual(resp.status_code, 401)

    def test_other_user_forbidden(self) -> None:
        ctx = self._setup()
        slot = self._slot_for(ctx["player_pos"])
        with self.app.test_client() as client:
            self._login(client, ctx["other_user_id"])
            resp = client.post(
                f"/api/team/{ctx['team_slug']}/line-sheet",
                json={"slots": {slot: ctx["player_id"]}, "roles": {}},
            )
            self.assertEqual(resp.status_code, 403)

    def test_gm_save_and_org_guard(self) -> None:
        ctx = self._setup()
        slot = self._slot_for(ctx["player_pos"])
        with self.app.test_client() as client:
            self._login(client, ctx["gm_id"])
            if ctx["other_id"] is not None:
                bad = client.post(
                    f"/api/team/{ctx['team_slug']}/line-sheet",
                    json={"slots": {slot: ctx["other_id"]}, "roles": {}},
                )
                self.assertEqual(bad.status_code, 400)
                self.assertIn("organization", bad.get_json().get("error", ""))
            ok = client.post(
                f"/api/team/{ctx['team_slug']}/line-sheet",
                json={"slots": {slot: ctx["player_id"]}, "roles": {str(ctx["player_id"]): "sniper"}},
            )
            self.assertEqual(ok.status_code, 200, ok.get_data(as_text=True))
            body = ok.get_json()
            self.assertTrue(body.get("ok"))
            self.assertEqual(int(body["slots"][slot]), ctx["player_id"])
            with self.app.app_context():
                row = db.session.scalar(
                    select(TeamLineSheet).where(
                        TeamLineSheet.league_slug == ctx["slug"],
                        TeamLineSheet.team_id == ctx["team_id"],
                    )
                )
                self.assertIsNotNone(row)
                self.assertEqual(row.slots_map().get(slot), ctx["player_id"])

    def test_admin_can_save(self) -> None:
        ctx = self._setup()
        slot = self._slot_for(ctx["player_pos"])
        with self.app.test_client() as client:
            self._login(client, ctx["admin_id"])
            resp = client.post(
                f"/api/team/{ctx['team_slug']}/line-sheet",
                json={"slots": {slot: ctx["player_id"]}, "roles": {}},
            )
            self.assertEqual(resp.status_code, 200, resp.get_data(as_text=True))


class LineBuilderTemplateTests(unittest.TestCase):
    def test_markers(self) -> None:
        root = Path(__file__).resolve().parents[1]
        team = (root / "app" / "templates" / "team.html").read_text(encoding="utf-8")
        partial = (root / "app" / "templates" / "_team_line_builder.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")
        js = (root / "app" / "static" / "js" / "site.js").read_text(encoding="utf-8")
        api = (root / "app" / "routes" / "api.py").read_text(encoding="utf-8")
        self.assertIn('_team_line_builder.html', team)
        self.assertIn("data-lines-view", team)
        self.assertIn("data-team-line-builder", partial)
        self.assertIn("Reset to imported", partial)
        self.assertIn("computed from FHM attributes", partial)
        self.assertIn('data-lb-kind="powerplay"', partial)
        self.assertIn('data-lb-kind="penalty"', partial)
        self.assertIn(".team-line-builder", css)
        self.assertIn("initTeamLineBuilder", js)
        self.assertIn("/team/<slug>/line-sheet", api)


if __name__ == "__main__":
    unittest.main()
