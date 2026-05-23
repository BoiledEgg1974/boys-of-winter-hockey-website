"""Playoff bracket opening-round projection helpers (all BOWL league sites)."""
from __future__ import annotations

import unittest

from app.services.playoff_bracket import (
    SeriesAgg,
    _merge_projected_empty_slots,
    _synthetic_preview_series,
)


class PlayoffBracketProjectionTests(unittest.TestCase):
    def test_merge_fills_only_empty_slots(self) -> None:
        real = SeriesAgg(
            team_a_id=1,
            team_b_id=2,
            wins_a=2,
            wins_b=1,
            games_played=3,
            first_date=None,
            last_date=None,
        )
        slots: list[SeriesAgg | None] = [real, None, None, None]
        projected = [
            _synthetic_preview_series(9, 10),
            _synthetic_preview_series(11, 12),
            _synthetic_preview_series(13, 14),
            _synthetic_preview_series(15, 16),
        ]
        out = _merge_projected_empty_slots(slots, projected)
        self.assertIs(out[0], real)
        for idx in (1, 2, 3):
            self.assertIs(out[idx], projected[idx])
            self.assertTrue(out[idx].preview_only)

    def test_merge_does_not_replace_existing_series(self) -> None:
        existing = _synthetic_preview_series(1, 2)
        projected = [_synthetic_preview_series(3, 4)]
        out = _merge_projected_empty_slots([existing], projected)
        self.assertIs(out[0], existing)


if __name__ == "__main__":
    unittest.main()
