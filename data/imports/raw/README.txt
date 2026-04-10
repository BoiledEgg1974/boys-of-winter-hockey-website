Raw FHM CSV exports: one subfolder per league (bowl_historical, bowl_fantasy, bowl_cap).

Optional: draft_info_supplement.csv in a league folder (same columns as draft_info.csv). Rows are merged on import when FHM omits a player from the main draft_info export.

Full update steps: see docs/DATA-UPDATE.md in the project root.

Quick import (from repo root, once per league):

  set LEAGUE_SLUG=bowl-fantasy
  python scripts/import_data.py

League slugs: bowl-historical, bowl-fantasy, bowl-cap.
