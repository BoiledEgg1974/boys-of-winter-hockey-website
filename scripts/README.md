# Scripts — update order (local → GitHub → live)

## Recommended (one command)

From the **repo root**:

```bash
python scripts/BOWL-Site-Update.py
# same as: python scripts/run_site_update.py bowl
```

This is the normal nightly path. It:

1. Imports FHM CSVs **locally** (including Historical awards alignment + racing when present).
2. Commits/pushes CSV + alignment files to GitHub.
3. Runs **`STEP2_pythonanywhere.py deploy-db`**: uploads league SQLite files (+ `app/static`), then on the server runs **`notify_discord_after_db_deploy.py`** (boxscores / BOWL Six / playoff bracket) and reloads WSGI.

League databases are **gitignored**. GitHub + a server `git pull` alone never refresh live scores, standings, or Discord queues.

Other workflows: **`python scripts/run_site_update.py --help`**

---

## What does *not* update the live site

These steps only refresh **code/CSVs** (or a backup). They do **not** replace `deploy-db`:

```bash
# NOT enough after BOWL-Site-Update / local import:
cd /home/BoiledEgg1974/boys-of-winter-hockey-website
git fetch origin && git checkout master && git reset --hard origin/master
pip install --upgrade -r requirements.txt
python scripts/backup_all_live_data.py
touch /var/www/www_bowlhockey_com_wsgi.py
```

After that checklist, the site still serves the **old** `instance/*.db` files, and Discord posts are **not** queued.

| Goal | Command |
|------|---------|
| Normal data + Discord update | `python scripts/BOWL-Site-Update.py` (includes `deploy-db`; `--deploy` is an explicit alias) |
| Data already imported locally; only push DBs + Discord | `python scripts/BOWL-Site-Update.py --deploy-db-only` |
| Same without the wrapper | `python scripts/STEP2_pythonanywhere.py deploy-db` |

---

## Same flow, manual steps

| Step | What to run |
|------|-------------|
| 1 | `python scripts/STEP1_update_from_saved_game.py --no-pa-deploy` (optional: `--allow-stale`, …) |
| 2 | Historical awards pass + re-import (or just use `BOWL-Site-Update.py`) |
| 3 | `git push` (BOWL-Site-Update does this unless `--no-push`) |
| 4 | **`python scripts/STEP2_pythonanywhere.py deploy-db`** — required for live DBs + Discord |

Legacy CSV upload + **server-side** import (also queues Discord during remote `import_data.py`):

```bash
python scripts/run_site_update.py to-live --yes-push
# or: python scripts/STEP2_pythonanywhere.py deploy --repo-csv
```

Prefer **`deploy-db`** for the usual BOWL update.

---

## PythonAnywhere bash (manual recovery only)

### Hard reset + new venv (rare)

Matches **`python scripts/STEP2_pythonanywhere.py deploy --full-remote-rebuild`** (after you `git push` so `origin/master` has what you want). Typical layout: venv at **`/home/BoiledEgg1974/venv`**, so `PA_REMOTE_VENV_BIN` should be **`/home/BoiledEgg1974/venv/bin`**. The script removes only the **`…/venv`** directory (the parent of `bin`), then recreates it — **not** your whole home folder.

Deploy reloads **`/var/www/www_bowlhockey_com_wsgi.py`** by default (and also touches
`/var/www/<user>_wsgi.py`). Override with **`PA_WSGI_FILE`** / **`--wsgi-file`** if needed.

### Imports only (after code + CSVs are already on the server)

Use this only when you intentionally import **on the server** instead of `deploy-db`. For League History **awards** and **all-stars**, run **`reimport_history_sheet_data.py`** after each league import. Discord boxscores enqueue during remote import; if you skipped import and only uploaded DBs, run **`notify_discord_after_db_deploy.py`** after promote:

```bash
cd /home/BoiledEgg1974/boys-of-winter-hockey-website
source /home/BoiledEgg1974/venv/bin/activate

export LEAGUE_SLUG=bowl-historical
python scripts/import_data.py
python scripts/reimport_history_sheet_data.py bowl-historical

export LEAGUE_SLUG=bowl-fantasy
python scripts/import_data.py
python scripts/reimport_history_sheet_data.py bowl-fantasy

export LEAGUE_SLUG=bowl-cap
python scripts/import_data.py
python scripts/reimport_history_sheet_data.py bowl-cap

# If you uploaded SQLite via deploy-db instead of importing here:
# python scripts/notify_discord_after_db_deploy.py

touch /var/www/www_bowlhockey_com_wsgi.py
```

---

## Still useful (not part of the default pipeline)

| Script | Purpose |
|--------|--------|
| `import_data.py` | Per-league importer (also used by STEP1 / STEP2 / `run_site_update`). |
| `notify_discord_after_db_deploy.py` | Queue boxscores / BOWL Six / bracket after `deploy-db` promotes league DBs. |
| `reset_db.py` | Wipe a league DB and re-import from scratch. |
| `reimport_history_awards.py` | Replace-only `history_awards` from CSV (optional `--only-award`). |
| `reimport_history_all_stars.py` | Additive upsert of `history_all_stars.csv` (never wipes existing / admin rows). |
| `snapshot_ovr_baseline.py` | OVR baseline snapshot (STEP1 / STEP2 call this). |
| `import_ap_catalog.py`, `verify_ap_catalog_sync.py`, `export_ap_catalog.py` | AP catalog maintenance. |
| `import_all.cmd`, `import_*.cmd` | Windows shortcuts to set `LEAGUE_SLUG` and run `import_data.py`. |
| `convert_trophy_history_sheet.py` | Spreadsheet → importer CSV helper used by STEP3. |
| `refresh_team_aggregates.py`, `backfill_skater_plus_minus.py` | Special fixes still wired into admin/CLI flows. |
| `backup_all_live_data.py` | Snapshot live DBs on the server (does not deploy or queue Discord). |

The **`import_pipeline/`** package is the core loader; do not remove it.

Legacy one-off repairs and diagnostics live under **`archive/one_off/`**. They are kept for reference
but are not part of the default update/deploy/runtime path.
