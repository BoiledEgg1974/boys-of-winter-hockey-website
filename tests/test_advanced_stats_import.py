"""Import mapping guards for advanced stats CSV columns."""
from __future__ import annotations

import unittest
from pathlib import Path


class AdvancedStatsImportTest(unittest.TestCase):
    def test_fhm_loader_maps_advanced_skater_columns(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "scripts" / "import_pipeline" / "fhm_loader.py").read_text(
            encoding="utf-8"
        )
        for token in (
            "row_db.cf_pct",
            "row_db.ff_pct",
            "row_db.gf_per_60",
            "gs.oz_starts",
            'setattr(gs, f"sq{i}"',
            "import_boxscore_penalties",
            'f"sq{i}_{side}"',
            "delete(PenaltyEvent)",
        ):
            self.assertIn(token, text)

    def test_models_define_advanced_columns(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "app" / "models.py").read_text(encoding="utf-8")
        for token in ("cf_pct", "oz_starts", "sq0_home", "gf_per_60"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main()
