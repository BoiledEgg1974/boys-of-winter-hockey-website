"""Regression: raw import dirs must always be Path before joining CSV filenames."""
from __future__ import annotations

from pathlib import Path

from app.config import BASE_DIR, coerce_raw_import_dir, league_raw_import_path


def test_league_raw_import_path_bowl_fantasy() -> None:
    p = league_raw_import_path("bowl-fantasy")
    assert isinstance(p, Path)
    assert p == BASE_DIR / "data" / "imports" / "raw" / "bowl_fantasy"


def test_coerce_raw_import_dir_bare_folder_name() -> None:
    p = coerce_raw_import_dir("bowl_fantasy")
    assert p == BASE_DIR / "data" / "imports" / "raw" / "bowl_fantasy"
    assert (p / "player_rights.csv").parent == p


def test_coerce_raw_import_dir_path_passthrough() -> None:
    full = league_raw_import_path("bowl-cap")
    assert coerce_raw_import_dir(full) == full
