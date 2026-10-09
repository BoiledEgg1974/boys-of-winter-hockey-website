#!/usr/bin/env python3
"""Create any missing site MySQL tables (e.g. after Perfect Squad schema adds).

Run on the VPS with league .env loaded:

  cd /srv/bowl/app && source .venv/bin/activate
  PYTHONPATH=/srv/bowl/perfect-squad python scripts/ensure_site_mysql_tables.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PS_ROOT = Path(os.environ.get("PERFECT_SQUAD_ROOT", "/srv/bowl/perfect-squad"))

if str(PS_ROOT) not in sys.path:
    sys.path.insert(0, str(PS_ROOT))


def main() -> int:
    from app import create_app, db
    import app.site_models  # noqa: F401

    app = create_app()
    with app.app_context():
        engine = db.get_engine(app, bind="site")
        db.metadatas["site"].create_all(bind=engine)
    print("Site MySQL create_all finished.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
