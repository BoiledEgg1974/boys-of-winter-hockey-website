from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
import unittest

from app.services.playoff_bracket import (
    _PROJECTED_PAIRINGS_8,
    _PROJECTED_PAIRINGS_4_HISTORICAL_DEFAULT,
    _playoff_window_has_started,
    _projected_series_for_seeded_rows,
    _slot_series_by_projected_opening_round,
    _synthetic_preview_series,
)
from app.services.playoff_seeding import (
    order_conference_by_playoff_seeding,
    seed_conference_with_division_winners,
)


class _Team:
    def __init__(self, name: str, *, conference_id: int = 0, division_id: int = 0) -> None:
        self.name = name
        self.fhm_conference_id = conference_id
        self.fhm_division_id = division_id

    def full_display_name(self) -> str:
        return self.name


def _standing(team_id: int, points: int, division_id: int, name: str):
    return SimpleNamespace(
        team_id=team_id,
        pts=points,
        w=points // 2,
        shootout_wins=0,
        gf=points,
        ga=0,
        division="",
        team=_Team(name, division_id=division_id),
    )


class PlayoffProjectionSeedingTest(unittest.TestCase):
    def test_conference_projection_seeds_division_winners_first(self) -> None:
        rows = [
            _standing(1, 100, 1, "Division A leader"),
            _standing(2, 99, 1, "Division A runner-up"),
            _standing(3, 80, 2, "Division B leader"),
            _standing(4, 79, 2, "Division B runner-up"),
            _standing(5, 70, 3, "Division C leader"),
            _standing(6, 69, 3, "Division C runner-up"),
            _standing(7, 68, 3, "Division C third"),
        ]

        seeded = seed_conference_with_division_winners(rows)

        self.assertEqual([st.team_id for st in seeded[:3]], [1, 3, 5])
        self.assertEqual([st.team_id for st in seeded[3:6]], [2, 4, 6])

    def test_conference_standings_order_keeps_all_teams_after_division_winners(self) -> None:
        rows = [
            _standing(1, 100, 1, "Division A leader"),
            _standing(2, 99, 1, "Division A runner-up"),
            _standing(3, 80, 2, "Division B leader"),
            _standing(4, 79, 2, "Division B runner-up"),
            _standing(5, 70, 3, "Division C leader"),
            _standing(6, 69, 3, "Division C runner-up"),
            _standing(7, 68, 3, "Division C third"),
        ]

        ordered = order_conference_by_playoff_seeding(rows)

        self.assertEqual([st.team_id for st in ordered], [1, 3, 5, 2, 4, 6, 7])

    def test_opening_round_real_series_follow_projected_seed_slots(self) -> None:
        rows = [
            _standing(1, 110, 1, "Division A leader"),
            _standing(2, 105, 1, "Division A runner-up"),
            _standing(3, 95, 2, "Division B leader"),
            _standing(4, 94, 2, "Division B runner-up"),
            _standing(5, 90, 3, "Division C leader"),
            _standing(6, 89, 3, "Division C runner-up"),
            _standing(7, 88, 3, "Division C third"),
            _standing(8, 87, 1, "Division A third"),
        ]
        seeded = seed_conference_with_division_winners(rows)
        projected = _projected_series_for_seeded_rows(seeded, _PROJECTED_PAIRINGS_8)
        real_schedule_order = [
            _synthetic_preview_series(5, 6),
            _synthetic_preview_series(3, 7),
            _synthetic_preview_series(1, 8),
            _synthetic_preview_series(2, 4),
        ]

        slots = _slot_series_by_projected_opening_round(real_schedule_order, projected)

        self.assertIsNotNone(slots)
        assert slots is not None
        self.assertEqual(
            [(s.team_a_id, s.team_b_id) for s in slots[:4] if s is not None],
            [(1, 8), (2, 4), (5, 6), (3, 7)],
        )

    def test_historical_default_pairs_first_seed_with_third(self) -> None:
        rows = [
            _standing(10, 100, 1, "Seed 1"),
            _standing(11, 90, 1, "Seed 2"),
            _standing(12, 80, 1, "Seed 3"),
            _standing(13, 70, 1, "Seed 4"),
        ]

        series = _projected_series_for_seeded_rows(rows, _PROJECTED_PAIRINGS_4_HISTORICAL_DEFAULT)

        self.assertEqual((series[0].team_a_id, series[0].team_b_id), (10, 12))
        self.assertEqual((series[1].team_a_id, series[1].team_b_id), (11, 13))

    def test_future_scheduled_playoff_games_do_not_start_official_bracket(self) -> None:
        future_game = SimpleNamespace(status="scheduled", game_date=date.today() + timedelta(days=5))
        today_game = SimpleNamespace(status="scheduled", game_date=date.today())

        self.assertFalse(_playoff_window_has_started([future_game]))
        self.assertTrue(_playoff_window_has_started([today_game]))


if __name__ == "__main__":
    unittest.main()
