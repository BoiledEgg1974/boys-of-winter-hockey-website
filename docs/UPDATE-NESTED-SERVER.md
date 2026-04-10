# Updating the site on a nested web server (ordered checklist)

Use this when the app is deployed behind a real web server (nginx, Apache, Caddy, PythonAnywhere, etc.) and URLs are **nested**—either because each league lives under its own path (`/bowl-fantasy/…`) or because the whole project sits under a parent path on your domain.

For **what the CSVs mean** and **full database reset**, see [DATA-UPDATE.md](DATA-UPDATE.md).

---

## 1. Know your public URLs

This project’s combined WSGI app (`wsgi.application`) serves:


| URL path             | What it is           |
| -------------------- | -------------------- |
| `/`                  | Hub / league picker  |
| `/bowl-historical/…` | BOWL-Historical site |
| `/bowl-fantasy/…`    | BOWL-Fantasy site    |
| `/bowl-cap/…`        | BOWL-Cap site        |


If your reverse proxy also uses a **prefix** (e.g. `https://example.com/hockey/bowl-fantasy/schedule`), the proxy must either:

- forward the **full path** after the domain to the app and set `**SCRIPT_NAME`** to that prefix (so links and `/api` calls resolve correctly), or  
- strip only your *infrastructure* prefix and still present paths that start with `/bowl-fantasy`, `/bowl-cap`, etc., to the WSGI app.

The HTML uses `request.script_root` for client-side paths; wrong proxy rules usually show broken navigation or 404s on inner pages.

---

## 2. Open a shell on the server

SSH in (or use your host’s console). Change to the **project root** (the folder that contains `wsgi.py`, `app/`, `hub/`, `data/`, `scripts/`).

---

## 3. Activate the same Python environment the site uses

Examples:

```bash
source /path/to/project/.venv/bin/activate
# or: source /path/to/venv/bin/activate
```

On Windows Server, use that venv’s `Scripts\activate`.

---

## 4. Update application code (only when you changed the repo)

If you deploy with Git:

```bash
git pull
```

If you upload a zip or rsync, sync files **except** overwriting live databases unless you intend to replace them:

- Safe to replace: `app/`, `hub/`, `wsgi.py`, `run.py`, `scripts/`, `requirements.txt`, etc.  
- Be careful: `instance/*.db` (your live data).

---

## 5. Install dependencies (only when `requirements.txt` changed)

```bash
pip install -r requirements.txt
```

---

## 6. Put new FHM CSVs on the server

Copy exported `.csv` files into the right folders (on the server):

- `data/imports/raw/bowl_historical/`
- `data/imports/raw/bowl_fantasy/`
- `data/imports/raw/bowl_cap/`

Overwrite or sync; the importer reads whatever is there.

---

## 7. Run the importer **once per league** you updated

From the **project root**, with the venv active:

```bash
export LEAGUE_SLUG=bowl-historical
python scripts/import_data.py

export LEAGUE_SLUG=bowl-fantasy
python scripts/import_data.py

export LEAGUE_SLUG=bowl-cap
python scripts/import_data.py
```

Skip leagues you did not refresh. On Windows cmd, use `set LEAGUE_SLUG=bowl-fantasy` before each `python scripts\import_data.py`.

Imports update SQLite under `instance/` and rebuild search indexes for that league.

---

## 8. Restart the WSGI / application process

The running workers still hold old code and old DB connections until they restart. Use **your host’s** method, for example:

- **systemd:** `sudo systemctl restart your-app.service`
- **gunicorn** under supervisord: `supervisorctl restart your-app`
- **Passenger:** `touch tmp/restart.txt` (or the path your host documents)
- **PythonAnywhere:** Web tab → **Reload** the web app

Point the process at `**wsgi.application`** from this project (not a single-league entry) unless you knowingly run one league only.

---

## 9. Verify in the browser (use the real nested URLs)

1. Open the hub: `https://your-domain/` (plus any site-wide prefix your proxy uses).
2. Open each league you imported: e.g. `…/bowl-fantasy/` → Standings or Schedule.
3. Try **player search** (confirms FTS rebuilt).
4. If API or theme looks wrong, re-check **proxy path** and `**SCRIPT_NAME`** for nested deployment.

---

## 10. Optional: static assets only

If you only added **logos or headshots** under `app/static/` and did **not** change CSVs, you may skip steps 6–7 and only restart (step 8) if your server caches static files aggressively.

---

## Quick reference


| Step | Action                                                        |
| ---- | ------------------------------------------------------------- |
| 1    | Confirm URL layout + proxy / `SCRIPT_NAME`                    |
| 2    | Shell → project root                                          |
| 3    | Activate venv                                                 |
| 4    | Pull/sync code if needed                                      |
| 5    | `pip install -r requirements.txt` if needed                   |
| 6    | Copy CSVs into `data/imports/raw/<league_folder>/`            |
| 7    | `LEAGUE_SLUG=<slug> python scripts/import_data.py` per league |
| 8    | Restart WSGI / web app                                        |
| 9    | Smoke-test hub + each league + search                         |


For a **from-scratch** server install, start with [DATA-UPDATE.md](DATA-UPDATE.md) “What you need on the server”, then use this checklist for every **update** cycle.