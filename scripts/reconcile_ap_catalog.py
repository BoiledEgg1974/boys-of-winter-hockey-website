#!/usr/bin/env python3
"""Run AP catalog seed/reconcile (adds missing default items, retires legacy duplicates)."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    from app import create_app
    from app.services.ap_service import seed_ap_catalog_if_empty

    app = create_app()
    with app.app_context():
        seed_ap_catalog_if_empty()
    print("AP catalog reconcile finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
