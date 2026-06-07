"""Player profile overview radar chart template guards."""
from __future__ import annotations

import unittest
from pathlib import Path


class PlayerProfileRadarTest(unittest.TestCase):
    def test_skater_overview_uses_radar_chart(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = (root / "app" / "templates" / "player.html").read_text(encoding="utf-8")
        css = (root / "app" / "static" / "css" / "site.css").read_text(encoding="utf-8")

        self.assertIn("Radar Chart", template)
        self.assertIn("player-profile__radar-svg", template)
        self.assertIn("player-profile__radar-player", template)
        self.assertIn("player-profile__radar-values", template)
        for key in ("skating", "shooting", "playmaking", "hockey_sense"):
            self.assertIn(f"'key':'{key}'", template)
        for class_name in (
            ".player-profile__radar-ring--outer",
            ".player-profile__radar-ring--high",
            ".player-profile__radar-ring--mid",
            ".player-profile__radar-ring--low",
            ".player-profile__radar-ring--base",
        ):
            self.assertIn(class_name, css)

    def test_supporting_rating_panels_stack_beside_radar(self) -> None:
        css = (
            Path(__file__).resolve().parents[1] / "app" / "static" / "css" / "site.css"
        ).read_text(encoding="utf-8")

        self.assertIn("grid-template-columns: minmax(190px, 240px) minmax(0, 1fr)", css)
        self.assertIn(".player-profile__skater-panel--overview", css)
        self.assertIn("grid-row: 1 / span 2", css)

    def test_position_ratings_use_compact_labels(self) -> None:
        template = (
            Path(__file__).resolve().parents[1] / "app" / "templates" / "player.html"
        ).read_text(encoding="utf-8")

        for full, abbr in (
            ("'Goalie': 'G'", "G"),
            ("'Left Defense': 'LD'", "LD"),
            ("'Right Defense': 'RD'", "RD"),
            ("'Left Wing': 'LW'", "LW"),
            ("'Center': 'C'", "C"),
            ("'Right Wing': 'RW'", "RW"),
        ):
            self.assertIn(full, template)
            self.assertIn(abbr, template)
        self.assertIn("SHT", template)
        self.assertIn("visually-hidden", template)

    def test_skater_category_labels_are_spelled_out(self) -> None:
        template = (
            Path(__file__).resolve().parents[1] / "app" / "templates" / "player.html"
        ).read_text(encoding="utf-8")

        self.assertIn("('Offense','off')", template)
        self.assertIn("('Defense','def')", template)
        self.assertIn("('Physical','phy')", template)
        self.assertIn("('Mental','men')", template)


if __name__ == "__main__":
    unittest.main()
