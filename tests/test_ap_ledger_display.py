"""AP ledger display helpers for GM/admin pages."""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app import create_app
from app.config import make_league_config
from app.services.ap_service import (
    LEDGER_KIND_EARNED,
    LEDGER_KIND_PENALIZED,
    LEDGER_KIND_REDEEMED,
    ledger_entry_description,
    ledger_entry_kind,
    news_article_ap_award,
    parse_ledger_list_params,
    scale_ap,
    standard_event_ap,
)
from app.services.bowl_six import (
    bowl_six_season_participation_ap,
    bowl_six_weekly_prize,
    season_ap_prize_for_rank,
)


class ApLedgerDisplayTests(unittest.TestCase):
    def test_ledger_entry_kind(self) -> None:
        self.assertEqual(ledger_entry_kind(3, "news_article"), LEDGER_KIND_EARNED)
        self.assertEqual(ledger_entry_kind(-2, "batch_penalties"), LEDGER_KIND_PENALIZED)
        self.assertEqual(ledger_entry_kind(-5, "redemption"), LEDGER_KIND_REDEEMED)

    def test_ledger_entry_description_prefers_note(self) -> None:
        desc = ledger_entry_description("manual", {"note": "EXPORT: +1 AP (BOWL-Cap)"})
        self.assertEqual(desc, "EXPORT: +1 AP (BOWL-Cap)")

    def test_ledger_entry_description_batch_label(self) -> None:
        desc = ledger_entry_description("batch_all_star", {"batch": "ALL-STAR"})
        self.assertEqual(desc, "ALL-STAR")

    def test_ledger_entry_description_redemption_lines(self) -> None:
        desc = ledger_entry_description(
            "redemption",
            {
                "request_id": 12,
                "lines": [{"title": "Extra roster spot", "cost": 4}],
            },
        )
        self.assertEqual(desc, "Extra roster spot")

    def test_parse_ledger_list_params_defaults(self) -> None:
        raw = MagicMock()
        raw.get.side_effect = lambda k, default=None: {
            "ledger_page": None,
            "ledger_team": None,
            "ledger_kind": None,
        }.get(k, default)
        page, team_id, kind = parse_ledger_list_params(raw)
        self.assertEqual((page, team_id, kind), (1, None, None))

    def test_parse_ledger_list_params_filters(self) -> None:
        raw = MagicMock()
        raw.get.side_effect = lambda k, default=None: {
            "ledger_page": "3",
            "ledger_team": "7",
            "ledger_kind": "earned",
        }.get(k, default)
        page, team_id, kind = parse_ledger_list_params(raw)
        self.assertEqual((page, team_id, kind), (3, 7, LEDGER_KIND_EARNED))

    def test_parse_ledger_list_params_locked_team(self) -> None:
        raw = MagicMock()
        raw.get.side_effect = lambda k, default=None: {
            "ledger_page": "2",
            "ledger_team": "99",
            "ledger_kind": "redeemed",
        }.get(k, default)
        page, team_id, kind = parse_ledger_list_params(raw, locked_team_id=12)
        self.assertEqual((page, team_id, kind), (2, 12, LEDGER_KIND_REDEEMED))


class ApEconomyScaleTests(unittest.TestCase):
    def test_earnings_rebase_at_10x(self) -> None:
        app = create_app(make_league_config("bowl-cap"))
        app.config["AP_ECONOMY_MULTIPLIER"] = 10
        app.config["NEWS_ARTICLE_AP_POINTS"] = 3
        with app.app_context():
            self.assertEqual(scale_ap(1), 10)
            self.assertEqual(standard_event_ap(), 10)
            self.assertEqual(news_article_ap_award(), 30)
            self.assertEqual(bowl_six_weekly_prize(1), 100)
            self.assertEqual(bowl_six_weekly_prize(2), 60)
            self.assertEqual(bowl_six_weekly_prize(3), 30)
            self.assertEqual(season_ap_prize_for_rank(1), 300)
            self.assertEqual(season_ap_prize_for_rank(4), 20)
            self.assertEqual(bowl_six_season_participation_ap(), 20)

    def test_rules_templates_use_scaled_amounts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        ap = (root / "app" / "templates" / "action_points.html").read_text(encoding="utf-8")
        ledger = (root / "app" / "templates" / "admin_ap_ledger.html").read_text(encoding="utf-8")
        self.assertIn("+{{ ap_event_points }} AP each", ap)
        self.assertIn("+{{ ap_article_points }} AP each", ap)
        self.assertIn("-{{ ap_event_points }} AP penalty each", ap)
        self.assertIn("award +{{ ap_event_points }} AP", ledger)
        self.assertIn("+{{ ap_event_points }} Action Points", ledger)
        self.assertNotIn("+1 AP each", ap)
        self.assertNotIn("award +1 AP", ledger)


if __name__ == "__main__":
    unittest.main()
