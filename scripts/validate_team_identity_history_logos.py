"""Validate logo_file paths in team_identity_history.csv against app/static."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import BASE_DIR, make_league_config
from app.services.import_validation import collect_team_identity_history_logo_issues


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Validate logo_file paths in team_identity_history.csv against app/static."
    )
    ap.add_argument(
        "--league",
        default="bowl-historical",
        help="League slug (e.g. bowl-historical, bowl-cap)",
    )
    args = ap.parse_args()
    cfg = make_league_config(args.league)
    raw_dir = Path(cfg.RAW_IMPORT_DIR)
    static_root = BASE_DIR / "app" / "static"
    csv_path = raw_dir / "team_identity_history.csv"
    if not csv_path.is_file():
        print(f"No file (nothing to validate): {csv_path}")
        return 0
    issues = collect_team_identity_history_logo_issues(raw_dir=raw_dir, static_root=static_root)
    if not issues:
        print(f"OK: all logo_file paths resolve under {static_root.resolve()}")
        return 0
    print(f"{len(issues)} issue(s) in {csv_path}:\n")
    for line in issues:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
