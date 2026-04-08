"""Delete SQLite DB so the next app start recreates tables (needed after schema changes)."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
slug = os.environ.get("LEAGUE_SLUG", "bowl-fantasy")
db = ROOT / "instance" / f"{slug}.db"
if db.is_file():
    db.unlink()
    print("Removed", db)
else:
    print("No database file at", db)
sys.exit(0)
