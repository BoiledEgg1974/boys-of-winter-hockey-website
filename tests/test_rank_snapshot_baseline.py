"""Tests for power/prospect rank CHG baseline selection."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services.power_rank_snapshots import apply_power_rank_trends
from app.services.rank_snapshot_baseline import select_rank_baseline_map
from app.site_models import PowerRankSnapshot


def _snap(ranks: dict[int, int], *, hours_ago: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(
        ranks_json=json.dumps({str(k): v for k, v in ranks.items()}, sort_keys=True),
        snapshot_at=datetime.utcnow() - timedelta(hours=hours_ago),
    )


class RankSnapshotBaselineTest(unittest.TestCase):
    def _select(self, current: dict[int, int], rows: list[SimpleNamespace]) -> dict[int, int]:
        scalars = MagicMock()
        scalars.all.return_value = rows
        session = MagicMock()
        session.scalars.return_value = scalars
        with patch("app.services.rank_snapshot_baseline.db") as mock_db:
            mock_db.session = session
            return select_rank_baseline_map("bowl-historical", current, PowerRankSnapshot)

    def test_falls_back_to_prior_when_latest_matches_current(self) -> None:
        current = {1: 1, 2: 2, 3: 3}
        prior = {1: 2, 2: 1, 3: 3}
        out = self._select(current, [_snap(current), _snap(prior, hours_ago=2)])
        self.assertEqual(out, prior)

    def test_skips_pre_expansion_prior_so_new_teams_are_not_stuck_on_new(self) -> None:
        """Expansion: latest has BUF/VAN; immediate prior is 12-team only.

        Falling back to the 12-team map would mark expansion clubs as NEW even after
        they move in the live table. Prefer the latest same-roster snap (CHG 0) until
        a compatible prior exists.
        """
        # 14-team live order (matches newest snapshot)
        current = {
            12: 1,
            14: 2,
            1: 3,
            10: 4,
            2: 5,
            9: 6,
            4: 7,
            7: 8,
            6: 9,
            11: 10,
            13: 11,
            8: 12,
            3: 13,
            5: 14,
        }
        pre_expansion = {
            1: 1,
            5: 2,
            9: 3,
            4: 4,
            6: 5,
            2: 6,
            11: 7,
            7: 8,
            3: 9,
            8: 10,
            10: 11,
            12: 12,
        }
        out = self._select(current, [_snap(current), _snap(pre_expansion, hours_ago=1)])
        self.assertEqual(out, current)
        self.assertIn(13, out)  # Buffalo
        self.assertIn(14, out)  # Vancouver

        teams = [
            {"team_id": tid, "power_score": 0}
            for tid, _rank in sorted(current.items(), key=lambda kv: kv[1])
        ]
        apply_power_rank_trends(teams, out)
        by_id = {int(t["team_id"]): t for t in teams}
        self.assertEqual(by_id[14]["trend_dir"], "same")
        self.assertEqual(by_id[13]["trend_dir"], "same")
        self.assertNotEqual(by_id[14]["trend_dir"], "new")
        self.assertNotEqual(by_id[13]["trend_dir"], "new")

    def test_walks_back_to_same_roster_prior_after_expansion(self) -> None:
        """After a second post-expansion snap, CHG should use the first 14-team order."""
        current = {1: 1, 2: 2, 13: 3, 14: 4}  # new order
        first_14 = {1: 2, 2: 1, 13: 4, 14: 3}  # earlier 14-team order
        pre_12 = {1: 1, 2: 2}
        out = self._select(
            current,
            [
                _snap(current),
                _snap(pre_12, hours_ago=0.5),  # incompatible; skip
                _snap(first_14, hours_ago=3),
            ],
        )
        self.assertEqual(out, first_14)

        teams = [{"team_id": tid} for tid, _ in sorted(current.items(), key=lambda kv: kv[1])]
        apply_power_rank_trends(teams, out)
        by_id = {int(t["team_id"]): t for t in teams}
        # team 13: prev 4 -> cur 3 => up +1
        self.assertEqual(by_id[13]["trend_dir"], "up")
        self.assertEqual(by_id[13]["trend_delta"], 1)
        # team 14: prev 3 -> cur 4 => down -1
        self.assertEqual(by_id[14]["trend_dir"], "down")
        self.assertEqual(by_id[14]["trend_delta"], 1)

    def test_uses_latest_when_not_recent_even_if_prior_differs(self) -> None:
        current = {1: 1, 2: 2}
        prior = {1: 2, 2: 1}
        out = self._select(
            current,
            [_snap(current, hours_ago=48), _snap(prior, hours_ago=50)],
        )
        self.assertEqual(out, current)


if __name__ == "__main__":
    unittest.main()
