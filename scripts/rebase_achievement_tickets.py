"""One-time rebase: multiply scratch-ticket cell values by AP_ECONOMY_MULTIPLIER (default 10).

Achievement tier multipliers (1/2/3 on the badge) are unchanged. Does not write AP ledger
rows — run after ``rebase_ap_economy.py`` on the shared site DB.

Examples::

  python scripts/rebase_achievement_tickets.py
  python scripts/rebase_achievement_tickets.py --apply --confirm
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import create_app
from app.league_db import db
from app.services.gm_achievements import (
    achievement_ticket_scale,
    apply_achievement_ticket_rebase_plan,
    build_achievement_ticket_rebase_plan,
    format_achievement_ticket_rebase_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebase GM achievement scratch ticket cell AP values.")
    parser.add_argument("--multiplier", type=int, default=0, help="Override AP_ECONOMY_MULTIPLIER from config")
    parser.add_argument("--apply", action="store_true", help="Apply updates (requires --confirm)")
    parser.add_argument("--confirm", action="store_true", help="Confirm apply")
    args = parser.parse_args()

    app = create_app()
    with app.app_context():
        scale = args.multiplier or achievement_ticket_scale()
        if scale <= 1:
            print("Multiplier is 1 — nothing to rebase. Set AP_ECONOMY_MULTIPLIER=10 in .env.", file=sys.stderr)
            return 1

        plan = build_achievement_ticket_rebase_plan(db.session, scale=scale)
        print(format_achievement_ticket_rebase_report(plan, scale))

        if not args.apply:
            print("\nDry run only. Pass --apply --confirm to update gm_achievement_unlocks rows.")
            return 0

        if not args.confirm:
            print("\nRefusing to apply without --confirm.", file=sys.stderr)
            return 1

        updated = apply_achievement_ticket_rebase_plan(db.session, plan)
        db.session.commit()
        print(f"\nUpdated {updated} achievement unlock row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
