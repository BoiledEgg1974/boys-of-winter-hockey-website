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
3. Runs **`STEP2_pythonanywhere.py deploy-db`**: uploads league SQLite files (+ `app/static`), then on the server runs **`notify_discord_after_db_deploy.py`** (boxscores / BOWL Six / playoff bracket) and reloads the web app (WSGI touch on PA, **`systemctl restart bowl-web`** on the VPS).

League databases are **gitignored**. GitHub + a server `git pull` alone never refresh live scores, standings, or Discord queues.

Other workflows: **`python scripts/run_site_update.py --help`**

---

## What does *not* update the live site

These steps only refresh **code/CSVs** (or a backup). They do **not** replace `deploy-db`:

```bash
# NOT enough after BOWL-Site-Update / local import (VPS example):
cd /srv/bowl/app
git fetch origin && git checkout master && git reset --hard origin/master
pip install --upgrade -r requirements.txt
python scripts/backup_all_live_data.py
sudo systemctl restart bowl-web
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

## Moving live hosting to a VPS

PythonAnywhere → nginx + gunicorn cutover (paths, rsync, DNS, Discord bot): **[docs/DEPLOY-VPS-NGINX.md §11](../docs/DEPLOY-VPS-NGINX.md#11-pythonanywhere--vps-cutover-runbook)**.

**Nightly deploy to DigitalOcean (production):** copy **`scripts/deploy-live-vps.env.example`** to **`scripts/deploy-live-vps.env`** (gitignored) or set the same variables in your shell. Then run the usual:

```bash
python scripts/BOWL-Site-Update.py
```

`BOWL-Site-Update.py` loads `deploy-live-vps.env` when present and STEP2 uses **`BOWL_WEB_RELOAD=systemd`** instead of touching PythonAnywhere WSGI. Override with `BOWL_DEPLOY_TARGET=pa` and `PA_HOST=ssh.pythonanywhere.com` if you ever push to PA again.

**Sync Python (GitHub → VPS)** when the droplet is not a git checkout (rsync snapshot). Does **not** upload `instance/*.db` or `.env`:

```bash
python scripts/sync_vps_app_code.py --dry-run
python scripts/sync_vps_app_code.py --pip --restart
```

Nightly **MariaDB + SQLite** backups on the VPS: `deploy/vps/bowl-backup.timer` (install with `bash /srv/bowl/app/deploy/vps/install-backup-timer.sh` as root).

**Off-droplet copies:** `python scripts/pull_vps_backups.py` (PC), or DO Spaces via `scripts/offsite-backup.env` + `python scripts/push_offsite_backup_env.py --test-sync`.

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

## DigitalOcean VPS bash (manual recovery)

DigitalOcean has **no** PythonAnywhere-style “run this bash on the server” button. Use:

- **Droplet → Access → Launch Droplet Console** (browser shell as root), or  
- **SSH** from your PC: `ssh -i ~/.ssh/id_ed25519_pa root@159.203.6.136`

Production app paths: **`/srv/bowl/app`**, venv **`/srv/bowl/app/.venv`**, web reload **`systemctl restart bowl-web`** (not WSGI touch). Discord bot: **`bowl-discord-bot.service`**.

The droplet **may not be a git clone**. If `git status` fails under `/srv/bowl/app`, sync code from your PC instead:

```powershell
python scripts/sync_vps_app_code.py --pip --restart
```

**Hard reset + venv (rare)** — prefer from your PC (same idea as old PA block):

```powershell
python scripts/STEP2_pythonanywhere.py deploy --full-remote-rebuild
```

If you must run on the **droplet** (only when `/srv/bowl/app` is a git checkout):

```bash
cd /srv/bowl/app
git fetch origin
git checkout master
git reset --hard origin/master
source /srv/bowl/app/.venv/bin/activate
pip install --upgrade -r requirements.txt
python -c "import flask, flask_login, flask_sqlalchemy, flask_wtf, pymysql; print('imports ok')"
# Guild id comes from /srv/bowl/app/.env — do not blank DISCORD_GUILD_ID unless you mean to:
python -m scripts.league_discord_bot.register_slash_commands
python scripts/backup_all_live_data.py
sudo systemctl restart bowl-web bowl-discord-bot
```

That checklist updates **code + deps** only. It does **not** refresh league SQLite from FHM or queue Discord boxscores. For a normal night, run on your PC:

```powershell
python scripts/BOWL-Site-Update.py
```

(`deploy-db` uploads DBs and runs Discord notify; STEP2 restarts `bowl-web` on the VPS.)

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
