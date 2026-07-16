"""Postseason Monte Carlo odds helpers."""
from __future__ import annotations

import random
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.postseason_odds import (
    _SimTeam,
    _conference_key_for_standing,
    _load_monte_carlo_context,
    _nhl_style_qualifiers,
    _rating,
    _single_monte_draw,
)


def _standing(
    team_id: int,
    *,
    pts: int,
    gp: int,
    w: int = 0,
    division: str = "Test Division",
    conference: str | None = None,
    fhm_conference_id: int | None = None,
) -> SimpleNamespace:
    team = SimpleNamespace(
        id=team_id,
        fhm_conference_id=fhm_conference_id,
        slug=f"team-{team_id}",
        abbreviation=f"T{team_id}",
        name=f"Team {team_id}",
    )
    team.full_display_name = lambda: team.name  # type: ignore[method-assign]
    st = SimpleNamespace(
        team_id=team_id,
        team=team,
        conference=conference,
        division=division,
        pts=pts,
        w=w,
        shootout_wins=0,
        gf=pts,
        ga=0,
        otl=0,
        l=0,
        t=0,
    )
    st.standing_gp_display = lambda: gp  # type: ignore[method-assign]
    return st


class PostseasonOddsTest(unittest.TestCase):
    def test_conference_key_falls_back_to_fhm_conference_id(self) -> None:
        st = _standing(1, pts=0, gp=0, conference=None, fhm_conference_id=0)
        self.assertEqual(_conference_key_for_standing(st, st.team), "conf:0")

    def test_rating_uses_simulated_games_played(self) -> None:
        leader = _SimTeam(1, "conf:0", "Div", gp=6, pts=12, w=6, sow=0, gf=20, ga=8)
        trailer = _SimTeam(2, "conf:0", "Div", gp=6, pts=4, w=2, sow=0, gf=8, ga=12)
        # After many simulated games, leader should still rate higher when GP is tracked.
        sim_gp = {1: 40, 2: 40}
        leader.pts = 70
        trailer.pts = 50
        self.assertGreater(_rating(leader, sim_gp), _rating(trailer, sim_gp))

    def test_leader_beats_trailer_in_small_monte_carlo(self) -> None:
        # One 15-team conference: only eight qualify; leader should beat cellar dweller.
        base_rows = [
            _SimTeam(1, "conf:0", "League", gp=6, pts=12, w=6, sow=0, gf=20, ga=8),
            _SimTeam(2, "conf:0", "League", gp=6, pts=4, w=2, sow=0, gf=8, ga=12),
        ] + [
            _SimTeam(
                i,
                "conf:0",
                "League",
                gp=6,
                pts=10 - (i % 5),
                w=5,
                sow=0,
                gf=18,
                ga=14,
            )
            for i in range(3, 16)
        ]
        base_by_id = {t.team_id: t for t in base_rows}
        remaining = [(1, 2), (2, 1)] * 15
        team_ids = [t.team_id for t in base_rows]
        counts = {
            tid: {
                "playoffs": 0,
                "division": 0,
                "conference": 0,
                "boiledegg": 0,
                "bowl_championship": 0,
            }
            for tid in team_ids
        }
        n = 200
        for sim_i in range(n):
            rng = random.Random(sim_i * 997 + 3)
            _single_monte_draw(
                rng,
                base_rows,
                base_by_id,
                remaining,
                two_conf=False,
                team_ids=team_ids,
                counts=counts,
                trace_tid=None,
                trace_pts=None,
                trace_gf=None,
                trace_ga=None,
            )
        self.assertGreater(counts[1]["playoffs"], counts[2]["playoffs"])

    def test_load_context_groups_fhm_conferences(self) -> None:
        session = MagicMock()
        standings = [
            _standing(i, pts=10 - i, gp=6, fhm_conference_id=0, division=f"Div {i % 3}")
            for i in range(8)
        ] + [
            _standing(i + 10, pts=10 - i, gp=6, fhm_conference_id=1, division=f"Div {i % 3}")
            for i in range(8)
        ]
        session.scalars.return_value.all.side_effect = [standings, []]
        teams_by_id = {st.team_id: st.team for st in standings}
        ctx = _load_monte_carlo_context(session, 1, teams_by_id)
        self.assertIsNotNone(ctx)
        base_rows, remaining, _base_by_id, two_conf, team_ids = ctx  # type: ignore[misc]
        self.assertTrue(two_conf)
        conf_keys = {t.conference for t in base_rows}
        self.assertEqual(conf_keys, {"conf:0", "conf:1"})
        self.assertEqual(len(team_ids), 16)
        self.assertEqual(remaining, [])

    def test_nhl_style_qualifiers_single_table_caps_at_eight(self) -> None:
        rows = [
            _SimTeam(i, "conf:0", "League", gp=6, pts=20 - i, w=10, sow=0, gf=30, ga=20)
            for i in range(15)
        ]
        qual = _nhl_style_qualifiers(rows)
        self.assertEqual(len(qual), 8)


if __name__ == "__main__":
    unittest.main()
