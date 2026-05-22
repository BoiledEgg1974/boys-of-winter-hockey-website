"""Draft Hub order generation from standings and pick ownership."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.services.draft_hub_order import (
    _standing_worst_first_key,
    generate_draft_order_from_prior_season,
    pick_ownership_lookup,
)


class DraftHubOrderTest(unittest.TestCase):
    def test_standing_worst_first_sorts_by_points(self) -> None:
        low = MagicMock(pts=50, w=20, gf=150, ga=200, team=MagicMock(full_display_name=lambda: "A"))
        high = MagicMock(pts=90, w=45, gf=250, ga=180, team=MagicMock(full_display_name=lambda: "B"))
        self.assertLess(_standing_worst_first_key(low), _standing_worst_first_key(high))

    def test_pick_ownership_lookup_maps_round_and_original_fhm(self) -> None:
        row = MagicMock(
            original_team_fhm_id=5,
            round=2,
            owner_team_id=102,
        )
        site = MagicMock()
        site.scalars.return_value.all.return_value = [row]
        out = pick_ownership_lookup(site, league_slug="bowl-historical", draft_year=1969)
        self.assertEqual(out[(2, 5)], 102)

    def test_generate_applies_traded_owner(self) -> None:
        draft = MagicMock(
            id=1,
            status="setup",
            timeline_year=1969,
            rounds=1,
            picks_per_round=2,
        )
        t_worst = MagicMock(id=10, fhm_team_id="5", fhm_league_id=None, full_display_name=lambda: "Worst")
        t_best = MagicMock(id=11, fhm_team_id="8", fhm_league_id=None, full_display_name=lambda: "Best")
        st_worst = MagicMock(pts=40, w=10, gf=100, ga=200, team=t_worst)
        st_best = MagicMock(pts=90, w=50, gf=250, ga=150, team=t_best)
        season = MagicMock(id=3, start_year=1968, label="1968-69")

        league = MagicMock()
        league.scalar.side_effect = [season]
        league.scalars.return_value.all.return_value = [st_worst, st_best]

        site = MagicMock()
        site.scalars.return_value.all.return_value = [
            MagicMock(original_team_fhm_id=5, round=1, owner_team_id=99),
        ]

        with unittest.mock.patch(
            "app.services.draft_hub_order.draft_pick_ownership_exists",
            return_value=True,
        ):
            created, err, summary = generate_draft_order_from_prior_season(
                league,
                site,
                league_slug="bowl-historical",
                draft=draft,
            )
        self.assertIsNone(err)
        self.assertEqual(created, 2)
        self.assertEqual(summary.get("traded_count"), 1)
        added = [call.args[0] for call in site.add.call_args_list]
        self.assertEqual(len(added), 2)
        first_slot = added[0]
        self.assertEqual(first_slot.original_team_id, 10)
        self.assertEqual(first_slot.team_id, 99)
        self.assertEqual(added[1].original_team_id, 11)
        self.assertEqual(added[1].team_id, 11)


if __name__ == "__main__":
    unittest.main()
