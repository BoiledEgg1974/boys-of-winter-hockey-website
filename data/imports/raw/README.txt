Raw FHM CSV exports: one subfolder per league (bowl_historical, bowl_fantasy, bowl_cap).

Optional: draft_info_supplement.csv in a league folder (same columns as draft_info.csv). Rows are merged on import when FHM omits a player from the main draft_info export.

Full update steps: see docs/DATA-UPDATE.md in the project root.

Quick import (from repo root, once per league):

  set LEAGUE_SLUG=bowl-fantasy
  python scripts/import_data.py

League slugs: bowl-historical, bowl-fantasy, bowl-cap.

hall_of_fame.csv (optional, per league folder)
  Re-import replaces all rows in the hall_of_fame_members table for that league DB.
  Columns (headers are normalized; aliases accepted):
    fhm_player_id OR player_id — FHM player id (same as history_awards.csv / Player.fhm_player_id).
    kind — optional: skater | goalie | g (if blank, G position → goalie, else skater).
    inducted_year OR inducted OR year — required (integer).
    sort_order OR order — optional integer; lower sorts first within the same inducted_year.
