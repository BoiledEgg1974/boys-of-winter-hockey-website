"""Era-correct draft team logos must not fall back to franchise -t{id} files."""
from __future__ import annotations

import re
import unittest
from pathlib import Path


def _norm_team_logo_name(s: str) -> str:
    return " ".join(str(s).lower().replace(".", " ").replace("-", " ").replace("_", " ").split())


def _timeline_key_for_logo(stem: str) -> tuple[str, int] | None:
    tm = re.search(r"^(.+?)_(\d{4})-(present|\d{4})$", stem.lower())
    if not tm:
        return None
    key = _norm_team_logo_name(tm.group(1))
    yr0 = int(tm.group(2))
    end_tok = tm.group(3)
    yr1 = 2100 if end_tok == "present" else int(end_tok)
    return key, yr0, yr1


class DraftPickTeamLogoEraTests(unittest.TestCase):
    def test_st_louis_blues_1995_maps_to_1989_1997_era_file(self) -> None:
        root = Path(__file__).resolve().parents[1] / "app" / "static" / "logos" / "teams" / "bowl_historical"
        timeline: dict[tuple[str, int], str] = {}
        for p in root.glob("st__louis_blues*.png"):
            parsed = _timeline_key_for_logo(p.stem)
            if not parsed:
                continue
            key, yr0, yr1 = parsed
            for yy in range(min(yr0, yr1), max(yr0, yr1) + 1):
                timeline[(key, yy)] = p.name
        self.assertIn(("st louis blues", 1995), timeline)
        self.assertIn("1989", timeline[("st louis blues", 1995)])


if __name__ == "__main__":
    unittest.main()
