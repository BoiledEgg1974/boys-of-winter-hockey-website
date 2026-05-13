# Scripts — update order (local → GitHub → live)

## Recommended (one command)

From the **repo root**:

```bash
python scripts/run_site_update.py to-live --yes-push
```

This runs, in order:

1. **`STEP1_update_from_saved_game.py --no-pa-deploy`** — snapshot OVR baselines, copy FHM CSVs into `data/imports/raw/`, **`STEP3_align_history_awards_to_player_master.py`** per league, then `import_data.py` + **`reimport_history_sheet_data.py`** per league. Optional git commit/push when you pass **`--yes-push`** (or answer the prompt).
2. **`STEP2_pythonanywhere.py deploy --repo-csv`** — upload raw CSVs + `app/static` + import helper, remote **`import_data.py`** and **`reimport_history_sheet_data.py`** per league, then touch WSGI.

Omit **`to-live`** if you like; it is the default workflow.

Other workflows: **`python scripts/run_site_update.py --help`**

---

## Same flow, manual steps

| Step | What to run |
|------|-------------|
| 1 | `python scripts/STEP1_update_from_saved_game.py --no-pa-deploy` (optional flags: `--yes-push`, `--allow-stale`, `--base`, …) |
| 2 | `git push` (or let STEP1 handle commit+push with `--yes-push`) |
| 3 | `python scripts/STEP2_pythonanywhere.py deploy --repo-csv` (optional: `--remote-pip`, `--dry-run`, …) |

**BOWL-Historical extra pass** (optional second STEP3 on Historical + re-import that league only): use **`python scripts/run_site_update.py bowl`** or **`python scripts/BOWL-Site-Update.py`**.

---

## PythonAnywhere bash (manual recovery)

### Hard reset + new venv (rare)

Matches **`python scripts/STEP2_pythonanywhere.py deploy --full-remote-rebuild`** (after you `git push` so `origin/master` has what you want). Typical layout: venv at **`/home/BoiledEgg1974/venv`**, so `PA_REMOTE_VENV_BIN` should be **`/home/BoiledEgg1974/venv/bin`**. The script removes only the **`…/venv`** directory (the parent of `bin`), then recreates it — **not** your whole home folder.

Adjust paths if your Web tab uses a different WSGI file than **`PA_WSGI_FILE`**.

### Imports only (after code + CSVs are already on the server)

`import_data.py` alone is still valid. For League History **awards** and **all-stars** to match the CSVs, run **`reimport_history_sheet_data.py`** after each league import (this is what STEP2 does automatically):

```bash
cd /home/BoiledEgg1974/boys-of-winter-hockey-website
source /home/BoiledEgg1974/venv/bin/activate   # or your project venv

export LEAGUE_SLUG=bowl-historical
python scripts/import_data.py
python scripts/reimport_history_sheet_data.py bowl-historical

export LEAGUE_SLUG=bowl-fantasy
python scripts/import_data.py
python scripts/reimport_history_sheet_data.py bowl-fantasy

export LEAGUE_SLUG=bowl-cap
python scripts/import_data.py
python scripts/reimport_history_sheet_data.py bowl-cap

touch /var/www/www_bowlhockey_com_wsgi.py   # use your real WSGI path
```

---

## Still useful (not part of the default pipeline)

| Script | Purpose |
|--------|--------|
| `import_data.py` | Per-league importer (also used by STEP1 / STEP2 / `run_site_update`). |
| `reset_db.py` | Wipe a league DB and re-import from scratch. |
| `reimport_history_awards.py` | Replace-only `history_awards` from CSV (optional `--only-award`). |
| `reimport_history_all_stars.py` | Replace-only `history_all_stars.csv`. |
| `snapshot_ovr_baseline.py` | OVR baseline snapshot (STEP1 / STEP2 call this). |
| `import_ap_catalog.py`, `verify_ap_catalog_sync.py`, `export_ap_catalog.py` | AP catalog maintenance. |
| `import_all.cmd`, `import_*.cmd` | Windows shortcuts to set `LEAGUE_SLUG` and run `import_data.py`. |
| `convert_trophy_history_sheet.py`, `convert_wide_all_stars_to_history_csv.py` | Spreadsheet → importer CSV helpers. |
| `diagnose_stats.py`, `refresh_team_aggregates.py`, `backfill_skater_plus_minus.py`, … | One-off fixes and diagnostics. |

The **`import_pipeline/`** package is the core loader; do not remove it.
