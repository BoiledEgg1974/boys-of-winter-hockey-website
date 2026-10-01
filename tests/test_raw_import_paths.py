"""Regression: raw import dirs must always be Path before joining CSV filenames."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from app.config import BASE_DIR, coerce_raw_import_dir, league_raw_import_path
from app.services.transfer_rules import compensation_cash_total_usd


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


def test_compensation_cash_total_usd() -> None:
    assert compensation_cash_total_usd('{"pta_transfer_fee": 500000, "cash_sweetener": 250000}') == 750_000


def test_bowl_team_budget_snapshot_subtracts_published_spend() -> None:
    from app.services.transfer_rules import bowl_team_budget_snapshot

    session = MagicMock()
    with patch("app.services.transfer_rules.resolve_transfer_salary_cap_usd", return_value=95_500_000):
        with patch("app.services.transfer_rules.transfer_budget_override_usd", return_value=10_000_000):
            with patch("app.services.transfer_rules.transfer_published_spend_usd", return_value=3_000_000):
                session.scalars.return_value.all.return_value = []
                snap = bowl_team_budget_snapshot(
                    session, bowl_team_id=1, league_slug="bowl-fantasy", spent_usd=3_000_000
                )
    assert snap["transfer_spent_usd"] == 3_000_000
    assert snap["transfer_wallet_remaining_usd"] == 7_000_000
    assert snap["remaining_budget_usd"] == 7_000_000
