"""Playoff bracket opening-round projection helpers (all BOWL league sites)."""
from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from app.services.playoff_bracket import (
    SeriesAgg,
    _current_postseason_games,
    _merge_projected_empty_slots,
    _reorder_mirror_round2_for_slots,
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

    def test_current_postseason_games_ignores_stale_playoffs_before_latest_regular_season(self) -> None:
        stale = SimpleNamespace(id=1, game_date=date(1968, 4, 1), game_type="Playoffs")
        regular = SimpleNamespace(id=2, game_date=date(1969, 3, 30), game_type="Regular Season")
        current = SimpleNamespace(id=3, game_date=date(1969, 4, 5), game_type="Playoffs")
        self.assertEqual(_current_postseason_games([stale, regular, current]), [current])

    def test_mirror_round_uses_division_fallback_when_conference_missing(self) -> None:
        west_a = SimpleNamespace(fhm_conference_id=-1, fhm_division_id=1)
        west_b = SimpleNamespace(fhm_conference_id=-1, fhm_division_id=1)
        east_a = SimpleNamespace(fhm_conference_id=-1, fhm_division_id=0)
        east_b = SimpleNamespace(fhm_conference_id=-1, fhm_division_id=0)
        west_series = _synthetic_preview_series(1, 2)
        east_series = _synthetic_preview_series(3, 4)
        teams = {1: west_a, 2: west_b, 3: east_a, 4: east_b}

        out = _reorder_mirror_round2_for_slots([east_series, west_series], teams)

        self.assertIs(out[0], west_series)
        self.assertIsNone(out[1])
        self.assertIs(out[2], east_series)
        self.assertIsNone(out[3])


if __name__ == "__main__":
    unittest.main()
