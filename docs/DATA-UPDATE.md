# Updating league data (any server)

This site reads **SQLite databases** built from **Franchise Hockey Manager (FHM) CSV exports**. There are **three leagues**; each has its own database file and its own folder of raw CSVs.

## What you need on the server

- **Python 3.11+** (or whatever version you use locally)
- Dependencies: `pip install -r requirements.txt`
- This repo: `app/`, `hub/`, `wsgi.py`, `data/imports/raw/`, `scripts/`, etc.

Optional: `pip install -r requirements-deploy.txt` only if you use the PythonAnywhere helper scripts.

## Where data lives


| Piece             | Location                                                                                                                                                                          |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FHM CSVs (source) | `data/imports/raw/bowl_historical/`, `bowl_fantasy/`, `bowl_cap/`                                                                                                                 |
| SQLite per league | `instance/bowl-historical.db`, `instance/bowl-fantasy.db`, `instance/bowl-cap.db` (legacy names like `league2.db` may still be used if the new file is empty—see `app/config.py`) |
| Team logos        | `app/static/logos/teams/<league_folder>/` (importer can copy from optional `team_logos` / `logos` inside each raw folder)                                                         |
| Player headshots  | `app/static/players/...` (optional)                                                                                                                                               |


League **URL slugs** (used in `LEAGUE_SLUG` and in URLs): `bowl-historical`, `bowl-fantasy`, `bowl-cap`.

## Normal update (after a new FHM export)

1. **Export CSVs from FHM** for each saved game (semicolon-separated bundle is supported when `team_data.csv` is present).
2. **Copy the `.csv` files** into the correct folder under `data/imports/raw/` (replace old files or sync the whole folder).
3. **Run the importer once per league** from the **project root**, with the environment variable set:
  **Windows (cmd):**
   **Linux / macOS:**
   Shortcut on Windows: `scripts\import_all.cmd` runs all three in order.
4. **Restart the web process** (whatever runs `wsgi.application`—gunicorn, uwsgi, Passenger, Waitress, etc.) so workers reload.

The import rebuilds the player search index (FTS) and related pieces automatically.

## Copy from FHM saved-game folders (Windows)

If your exports still live under each game’s `import_export\csv` folder, you can use:

```bat
python scripts\STEP1_update_from_saved_game.py
```

**STEP 1** — copy FHM exports into `data/imports/raw/…`, run `import_data.py` locally per league, optional Git push, optional PythonAnywhere deploy (`--pa-deploy` chains **STEP 2**).

The first time, answer **y** when asked if paths changed, and paste each league’s CSV folder. Paths are stored in `scripts/saved_game_csv_paths.json` (local only; listed in `.gitignore`). Or pass a single base folder that contains `bowl_historical`, `bowl_fantasy`, and `bowl_cap` subfolders.

Non-interactive options: `python scripts/STEP1_update_from_saved_game.py --help`.

## Full reset (empty DB, then import again)

Only when you need a clean schema or things are badly out of sync:

```bat
set LEAGUE_SLUG=bowl-fantasy
python scripts\reset_db.py
python scripts\import_data.py
```

Repeat with each `LEAGUE_SLUG`. **This wipes that league’s database.**

## Running the site

- **Local:** `python run.py` → hub at `/`, each league at `/bowl-historical/`, `/bowl-fantasy/`, `/bowl-cap/`.
- **Production:** point your WSGI server at `wsgi.application` from this project. The reverse proxy must **pass the full URL path** (do not strip the league prefix).

## Environment variables (summary)


| Variable       | Purpose                                                                                                                 |
| -------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `LEAGUE_SLUG`  | Which league to import or which single-league app config to use                                                         |
| `DATABASE_URL` | Optional; overrides SQLite path for the default single-app setup (multi-mount `wsgi.py` uses per-league config instead) |
| `SECRET_KEY`   | Flask session security in production                                                                                    |


## Optional scripts (not required for a normal update)

- `scripts/refresh_team_aggregates.py`, `backfill_skater_plus_minus.py`, `import_skater_career_csvs.py` — special fixes; use only if you know you need them.
- `scripts/STEP2_pythonanywhere.py` / `Deploy-To-PythonAnywhere.bat` — **STEP 2**: SFTP CSVs + `app/static` (newer-only), remote `import_data.py` per league, WSGI reload. Shared importer remains `scripts/import_data.py`.

For more detail on FHM file names and behavior, see `scripts/import_pipeline/runner.py` and `scripts/import_pipeline/fhm_loader.py`.

**Nested production server:** step-by-step order of operations → [UPDATE-NESTED-SERVER.md](UPDATE-NESTED-SERVER.md).