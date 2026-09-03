"""NHL-style Draft Hub lottery math, order rewrite, and admin guards."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import create_app
from app.config import make_league_config
from app.services.draft_lottery import (
    ASSIGNED_COMBO_COUNT,
    NHL_16_WEIGHTS,
    STATUS_COMPLETE,
    STATUS_LOCKED_1,
    STATUS_PENDING,
    LotterySeed,
    all_combinations,
    apply_lottery_round1_order,
    apply_lottery_to_slots,
    arm_lottery,
    combo_key,
    draw_combination,
    execute_draw,
    gm_picks_blocked_by_lottery,
    lottery_is_complete,
    lottery_seed_teams,
    odds_matrix,
    reset_lottery,
    scale_combo_counts,
)
from app.services.draft_hub_order import generate_draft_order_from_ranking


class _ChoiceRng:
    def __init__(self, keys: list[str]) -> None:
        self.keys = list(keys)

    def choice(self, pool):
        for key in self.keys:
            if key in pool:
                self.keys = [k for k in self.keys if k != key]
                return key
        return pool[0]

    def shuffle(self, items: list) -> None:
        return None


class DraftLotteryMathTest(unittest.TestCase):
    def test_combination_universe_is_1001(self) -> None:
        combos = all_combinations()
        self.assertEqual(len(combos), 1001)
        self.assertEqual(combos[0], (1, 2, 3, 4))
        self.assertEqual(combos[-1], (11, 12, 13, 14))

    def test_nhl_16_weights_match_and_sum_1000(self) -> None:
        counts = scale_combo_counts(16)
        self.assertEqual(counts, list(NHL_16_WEIGHTS))
        self.assertEqual(sum(counts), ASSIGNED_COMBO_COUNT)

    def test_scaled_fields_still_sum_1000(self) -> None:
        for n in (8, 18):
            counts = scale_combo_counts(n)
            self.assertEqual(len(counts), n, msg=n)
            self.assertEqual(sum(counts), ASSIGNED_COMBO_COUNT, msg=n)
            self.assertTrue(all(c >= 1 for c in counts), msg=n)
            self.assertGreater(counts[0], counts[-1])

    def test_odds_seed_one_only_lands_top_three(self) -> None:
        counts = scale_combo_counts(16)
        rows = odds_matrix(counts, 2)
        self.assertAlmostEqual(rows[0]["pick1_pct"], 25.5, places=1)
        pcts = rows[0]["pick_pcts"]
        self.assertGreater(pcts[0], 0)
        self.assertGreater(pcts[1], 0)
        self.assertGreater(pcts[2], 0)
        self.assertTrue(all(p == 0 for p in pcts[3:]))
        self.assertAlmostEqual(rows[-1]["pick1_pct"], 0.5, places=1)

    def test_unused_combo_is_redrawn(self) -> None:
        unused = "11-12-13-14"
        combo_to_seed = {"1-2-3-4": 1, "1-2-3-5": 2}
        rng = _ChoiceRng([unused, "1-2-3-5"])
        combo, seed = draw_combination(combo_to_seed, unused, set(), rng)
        self.assertEqual(combo, (1, 2, 3, 5))
        self.assertEqual(seed, 2)

    def test_draw_two_cannot_repeat_winner(self) -> None:
        unused = "11-12-13-14"
        combo_to_seed = {"1-2-3-4": 1, "1-2-3-5": 2, "1-2-3-6": 3}
        rng = _ChoiceRng(["1-2-3-4", "1-2-3-4", "1-2-3-6"])
        combo, seed = draw_combination(combo_to_seed, unused, {1}, rng)
        self.assertEqual(seed, 3)
        self.assertEqual(combo, (1, 2, 3, 6))

    def test_lottery_seed_teams_takes_worst_n_unique(self) -> None:
        self.assertEqual(lottery_seed_teams([10, 10, 11, 12, 13], team_count=3), [10, 11, 12])

    def test_apply_order_winners_then_remaining_seeds_then_tail(self) -> None:
        seeds = [
            LotterySeed(1, 101, 201, 255),
            LotterySeed(2, 102, 202, 135),
            LotterySeed(3, 103, 203, 115),
            LotterySeed(4, 104, 204, 95),
        ]
        tail = [LotterySeed(5, 105, 205, 0)]
        ordered = apply_lottery_round1_order(seeds, [4, 2], tail)
        self.assertEqual([s.original_team_id for s in ordered], [104, 102, 101, 103, 105])
        self.assertEqual([s.owner_team_id for s in ordered], [204, 202, 201, 203, 205])

    def test_traded_first_keeps_owner_and_original(self) -> None:
        seeds = [LotterySeed(1, 10, 99, 255), LotterySeed(2, 20, 20, 135)]
        ordered = apply_lottery_round1_order(seeds, [1], [])
        self.assertEqual(ordered[0].original_team_id, 10)
        self.assertEqual(ordered[0].owner_team_id, 99)


class DraftLotteryServiceGuardTest(unittest.TestCase):
    def test_execute_draw_rejected_when_complete(self) -> None:
        session = MagicMock()
        draft = SimpleNamespace(id=1, status="setup")
        lottery = SimpleNamespace(status=STATUS_COMPLETE, draw_count=2, draws_json="[]")
        with (
            patch("app.services.draft_lottery.get_lottery", return_value=lottery),
            patch("app.services.draft_lottery.draft_has_picks", return_value=False),
        ):
            _row, _result, err = execute_draw(session, draft)
        self.assertEqual(err, "Lottery is already complete.")

    def test_execute_draw_rejected_after_picks(self) -> None:
        session = MagicMock()
        draft = SimpleNamespace(id=1, status="live")
        lottery = SimpleNamespace(status=STATUS_PENDING, draw_count=2, draws_json="[]")
        with (
            patch("app.services.draft_lottery.get_lottery", return_value=lottery),
            patch("app.services.draft_lottery.draft_has_picks", return_value=True),
        ):
            _row, _result, err = execute_draw(session, draft)
        self.assertEqual(err, "Lottery cannot run after a pick has been made.")

    def test_reset_rejected_after_picks(self) -> None:
        session = MagicMock()
        draft = SimpleNamespace(id=1, status="setup")
        lottery = SimpleNamespace(status=STATUS_LOCKED_1)
        with (
            patch("app.services.draft_lottery.get_lottery", return_value=lottery),
            patch("app.services.draft_lottery.draft_has_picks", return_value=True),
        ):
            _row, err = reset_lottery(session, draft)
        self.assertEqual(err, "Lottery cannot be reset after a pick has been made.")

    def test_arm_rejected_after_picks(self) -> None:
        session = MagicMock()
        draft = SimpleNamespace(id=1, status="setup")
        with patch("app.services.draft_lottery.draft_has_picks", return_value=True):
            _row, err = arm_lottery(session, draft)
        self.assertEqual(err, "Lottery cannot be armed after a pick has been made.")

    def test_gm_picks_blocked_until_complete_on_relegation(self) -> None:
        session = MagicMock()
        draft = SimpleNamespace(id=7, league_slug="bowl-fantasy")
        with patch("app.services.draft_lottery.get_lottery", return_value=None):
            self.assertTrue(gm_picks_blocked_by_lottery(session, draft))
        complete = SimpleNamespace(status=STATUS_COMPLETE)
        with patch("app.services.draft_lottery.get_lottery", return_value=complete):
            self.assertFalse(gm_picks_blocked_by_lottery(session, draft))
        other = SimpleNamespace(id=7, league_slug="bowl-cap")
        self.assertFalse(gm_picks_blocked_by_lottery(session, other))

    def test_lottery_is_complete_helper(self) -> None:
        self.assertFalse(lottery_is_complete(None))
        self.assertTrue(lottery_is_complete(SimpleNamespace(status=STATUS_COMPLETE)))

    def test_apply_slots_leaves_later_rounds_untouched(self) -> None:
        r1a = SimpleNamespace(overall_pick=1, round=1, original_team_id=1, team_id=1)
        r1b = SimpleNamespace(overall_pick=2, round=1, original_team_id=2, team_id=2)
        r2 = SimpleNamespace(overall_pick=3, round=2, original_team_id=1, team_id=1)
        lottery = SimpleNamespace(
            seeds_json='[{"seed":1,"original_team_id":2,"owner_team_id":9,"combo_count":255},'
            '{"seed":2,"original_team_id":1,"owner_team_id":1,"combo_count":135}]',
            draws_json='[{"seed":1}]',
            tail_json="[]",
        )
        session = MagicMock()
        draft = SimpleNamespace(id=1)
        with (
            patch("app.services.draft_lottery.draft_has_picks", return_value=False),
            patch("app.services.draft_lottery._round1_slots", return_value=[r1a, r1b]),
        ):
            err = apply_lottery_to_slots(session, draft, lottery)
        self.assertIsNone(err)
        self.assertEqual(r1a.original_team_id, 2)
        self.assertEqual(r1a.team_id, 9)
        self.assertEqual(r2.original_team_id, 1)
        self.assertEqual(r2.team_id, 1)

    def test_generate_ranking_rejects_short_list(self) -> None:
        draft = SimpleNamespace(status="setup", picks_per_round=16, rounds=1, timeline_year=1987, id=1)
        created, err, _summary = generate_draft_order_from_ranking(
            MagicMock(),
            MagicMock(),
            league_slug="bowl-fantasy",
            draft=draft,
            ranking_team_ids=[1, 2, 3],
        )
        self.assertEqual(created, 0)
        self.assertIn("Rank at least 16", err or "")


class DraftLotteryRouteGuardTest(unittest.TestCase):
    def test_anonymous_draw_is_rejected(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_client() as client:
            res = client.post("/draft-hub/admin/lottery/draw", json={"csrf_token": "x"})
        self.assertIn(res.status_code, (302, 400, 401, 403))

    def test_anonymous_reset_is_rejected(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_client() as client:
            res = client.post("/draft-hub/admin/lottery/reset", json={"csrf_token": "x"})
        self.assertIn(res.status_code, (302, 400, 401, 403))

    def test_preview_page_is_public(self) -> None:
        app = create_app(make_league_config("bowl-fantasy"))
        with app.test_client() as client:
            res = client.get("/draft-lottery/preview")
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("Draft lottery preview", body)
        self.assertIn("dh-lottery-preview-data", body)
        self.assertIn("data-preview=\"1\"", body)
        self.assertIn("data-practice-allowed=\"0\"", body)
        self.assertIn('"can_admin":false', body.replace(" ", ""))

    def test_preview_practice_allowed_for_gm(self) -> None:
        import uuid

        from app.league_db import db
        from app.site_models import GmLeagueMembership, User

        app = create_app(make_league_config("bowl-fantasy"))
        gm_email = f"lotto-gm-{uuid.uuid4().hex}@example.invalid"
        with app.app_context():
            db.create_all(bind_key="site")
            gm = User(
                email=gm_email,
                password_hash="x",
                discord_name="GM",
                is_admin=False,
                admin_role=None,
            )
            db.session.add(gm)
            db.session.flush()
            db.session.add(
                GmLeagueMembership(
                    league_slug="bowl-fantasy",
                    user_id=int(gm.id),
                    team_id=1,
                    status="active",
                )
            )
            db.session.commit()
            gm_id = int(gm.id)

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["_user_id"] = str(gm_id)
                sess["_fresh"] = True
            res = client.get("/draft-lottery/preview")
        self.assertEqual(res.status_code, 200)
        body = res.get_data(as_text=True)
        self.assertIn("data-practice-allowed=\"1\"", body)
        self.assertIn('"can_admin":false', body.replace(" ", ""))

    def test_combo_key_sorts_balls(self) -> None:
        self.assertEqual(combo_key([12, 4, 9, 5]), "4-5-9-12")


if __name__ == "__main__":
    unittest.main()
