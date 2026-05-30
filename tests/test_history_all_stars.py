"""League History all-star display helpers."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.history_all_stars import build_history_all_stars_bundle


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self, _statement):
        return _Scalars(self._rows)


def _row(label: str, related_start_year: int, team_rank: int, slot: int = 1):
    return SimpleNamespace(
        season_label=label,
        notes=None,
        season=SimpleNamespace(start_year=related_start_year, label=label),
        team_rank=team_rank,
        slot=slot,
    )


class HistoryAllStarsTests(unittest.TestCase):
    def test_default_season_uses_latest_all_star_label(self) -> None:
        rows = [
            _row("1967-68", 1968, 1),
            _row("1967-68", 1968, 2),
            _row("1968-69", 1967, 1),
            _row("1968-69", 1967, 2),
        ]

        with patch("app.services.history_all_stars.attach_history_all_star_season_teams"):
            bundle = build_history_all_stars_bundle(_Session(rows), None)  # type: ignore[arg-type]

        self.assertEqual(bundle["selected"], "1968-69")
        self.assertEqual(bundle["season_labels"][:2], ["1968-69", "1967-68"])


if __name__ == "__main__":
    unittest.main()
