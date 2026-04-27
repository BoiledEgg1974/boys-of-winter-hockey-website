"""Export AP redemption catalog rows from a site SQLite database to JSON."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export ap_redemption_catalog rows to a JSON file.",
    )
    parser.add_argument(
        "--db",
        default="instance/site_membership.db",
        help="Path to site_membership.db (default: instance/site_membership.db).",
    )
    parser.add_argument(
        "--out",
        default="ap_redemption_catalog_live.json",
        help="Output JSON path (default: ap_redemption_catalog_live.json).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db)
    out_path = Path(args.out)
    if not db_path.is_file():
        raise SystemExit(f"Database file not found: {db_path}")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT league_group, sort_order, title, description, cost_ap, is_active
                FROM ap_redemption_catalog
                ORDER BY league_group, cost_ap, sort_order, id
                """
            )
        ]
    finally:
        conn.close()

    out_path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"Exported {len(rows)} rows -> {out_path}")


if __name__ == "__main__":
    main()
