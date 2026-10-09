# Deploy BOWL on a VPS (nginx + gunicorn)

This guide moves the combined hub + three league sites off shared hosting (e.g. PythonAnywhere) onto a small Linux VPS with **nginx** in front and **gunicorn** running `wsgi.application`. The app layout is unchanged: `/`, `/bowl-historical/`, `/bowl-fantasy/`, `/bowl-cap/`.

For CSV import and update cycles after the server exists, keep using [DATA-UPDATE.md](DATA-UPDATE.md) and [UPDATE-NESTED-SERVER.md](UPDATE-NESTED-SERVER.md) (steps 6–9).

---

## 1. VPS sizing (starting point)

| Resource | Suggestion |
| -------- | ---------- |
| RAM | **2 GB** minimum (3 league SQLite DBs + site DB + gunicorn workers) |
| CPU | 1–2 vCPU |
| Disk | 20 GB+ (CSV imports, `instance/*.db`, `instance/league_json_cache/`, static assets) |
| OS | Ubuntu 22.04 or 24.04 LTS |

---

## 2. Server packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx git
```

Optional: `certbot python3-certbot-nginx` for Let’s Encrypt.

---

## 3. App user and checkout

```bash
sudo adduser --disabled-password bowl
sudo mkdir -p /srv/bowl
sudo chown bowl:bowl /srv/bowl
sudo -u bowl git clone https://github.com/YOUR_ORG/Boys-Of-Winter-League.git /srv/bowl/app
cd /srv/bowl/app
sudo -u bowl python3 -m venv .venv
sudo -u bowl .venv/bin/pip install -r requirements.txt
sudo -u bowl .venv/bin/pip install gunicorn
```

Copy production env (never commit secrets):

```bash
sudo -u bowl cp .env.example .env
# edit /srv/bowl/app/.env — see section 5
```

Ensure live databases and CSVs exist under `instance/` and `data/imports/raw/` (rsync or your existing deploy scripts).

---

## 4. Environment variables (VPS)

In `/srv/bowl/app/.env` (loaded by the app via `python-dotenv` where configured, or export from systemd):

```bash
FLASK_DEBUG=0
SECRET_KEY=<long-random-string>
SITE_PUBLIC_BASE_URL=https://www.bowlhockey.com

# Recommended on a dedicated VPS (pre-build homepage JSON after worker start / import)
LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1

# Optional: paint leaders + standings in HTML from cache (no cold build on home HTML)
HOMEPAGE_SSR_LEADERS_STANDINGS=1

# Site DB — keep MySQL if you already use PA MySQL, or SQLite on VPS:
# SITE_DATABASE_URL=mysql+pymysql://...
```

Leave **`LEAGUE_JSON_CACHE_WARM_ON_STARTUP=0`** on PythonAnywhere if you stay on PA (many workers × three leagues can spike SQLite I/O on reload).

---

## 5. systemd unit (gunicorn)

Create `/etc/systemd/system/bowl-web.service`:

```ini
[Unit]
Description=BOWL combined WSGI (hub + leagues)
After=network.target

[Service]
User=bowl
Group=bowl
WorkingDirectory=/srv/bowl/app
EnvironmentFile=/srv/bowl/app/.env
Environment=PYTHONPATH=/srv/bowl/app
ExecStart=/srv/bowl/app/.venv/bin/gunicorn \
  --bind 127.0.0.1:8000 \
  --workers 3 \
  --threads 2 \
  --timeout 120 \
  --worker-class gthread \
  --access-logfile - \
  --error-logfile - \
  wsgi:application
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Notes:

- **`wsgi:application`** is the combined DispatcherMiddleware app (same as PythonAnywhere).
- **`--workers 3`**: one process can lazy-load each league; 3–4 workers is a reasonable start on 2 GB RAM. Increase only if CPU is idle and RAM allows.
- **`--timeout 120`**: cold homepage cache builds can be slow; warm cache keeps requests fast.
- First request to a league after a worker fork still pays lazy `create_app` (see `wsgi.py`); warming mitigates JSON endpoints, not that one-time init.

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable bowl-web
sudo systemctl start bowl-web
sudo systemctl status bowl-web
```

---

## 6. nginx site

Create `/etc/nginx/sites-available/bowl`:

```nginx
upstream bowl_app {
    server 127.0.0.1:8000;
    keepalive 8;
}

server {
    listen 80;
    server_name www.bowlhockey.com bowlhockey.com;

    client_max_body_size 20M;

    # Long-cache static assets (combined static middleware + app/static)
    location ~* ^/(bowl-historical|bowl-fantasy|bowl-cap|bowl-formula|bowl-demolition)?/static/ {
        alias /srv/bowl/app/app/static/;
        try_files $uri @app;
        expires 7d;
        add_header Cache-Control "public, max-age=604800";
    }

    location / {
        proxy_pass http://bowl_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;
    }

    location @app {
        proxy_pass http://bowl_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable and test:

```bash
sudo ln -sf /etc/nginx/sites-available/bowl /etc/nginx/sites-enabled/bowl
sudo nginx -t
sudo systemctl reload nginx
```

TLS: `sudo certbot --nginx -d www.bowlhockey.com -d bowlhockey.com`

**Path layout:** nginx must forward the **full** path (`/bowl-fantasy/...`) to gunicorn without stripping the league prefix. Do **not** set `SCRIPT_NAME` unless you use an extra URL prefix above the league slugs (see [UPDATE-NESTED-SERVER.md](UPDATE-NESTED-SERVER.md)).

Discord interactions and hub routes use **`SITE_PUBLIC_BASE_URL`** at the domain root (e.g. `https://www.bowlhockey.com/api/discord/interactions`), not under a league prefix.

---

## 7. Post-deploy cache warm (recommended)

On the VPS, **`bowl-cache-warm.service`** runs automatically after each `bowl-web` restart (`ExecStartPost` → `deploy/vps/warm-league-json-cache.sh` hits all three homepage summary APIs). Logs: `journalctl -u bowl-cache-warm -n 30`.

Manual warm (optional):

```bash
sudo systemctl start bowl-cache-warm.service
# or from the app tree:
sudo -u bowl /srv/bowl/app/deploy/vps/warm-league-json-cache.sh
```

Also set `LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1` and `HOMEPAGE_SSR_LEADERS_STANDINGS=1` in `.env` (see section 4).

---

## 8. uWSGI instead of gunicorn (optional)

If you prefer uWSGI:

```bash
pip install uwsgi
```

Example ini:

```ini
[uwsgi]
module = wsgi:application
chdir = /srv/bowl/app
virtualenv = /srv/bowl/app/.venv
master = true
processes = 3
threads = 2
socket = 127.0.0.1:8000
chmod-socket = 660
vacuum = true
die-on-term = true
harakiri = 120
env = PYTHONPATH=/srv/bowl/app
```

Point nginx `proxy_pass` at the socket or HTTP listener. Gunicorn is usually simpler for Flask; both work with the same `wsgi:application`.

---

## 9. Discord bot and cron on the VPS

Run the league Discord bot as a **separate** systemd service (same repo, same `.env`), pointing `DISCORD_BOT_LEAGUE_BASE_URLS` at your public URLs.

Keep using `scripts/run_site_update.py` from your dev machine or a CI runner; on the VPS you only need git pull / rsync DBs, imports, and `systemctl restart bowl-web`.

### Code sync (when `/srv/bowl/app` is not a git checkout)

```bash
python scripts/sync_vps_app_code.py --dry-run
python scripts/sync_vps_app_code.py --pip --restart
```

Excludes `instance/*.db`, `.env`, and `instance/league_json_cache/`. Use **`deploy-db`** for live league data.

### Nightly backups (MariaDB + SQLite)

On the droplet as root, after the backup scripts exist under `deploy/vps/`:

```bash
bash /srv/bowl/app/deploy/vps/install-backup-timer.sh
systemctl start bowl-backup.service   # optional test
```

Files land in `/srv/bowl/backups/` (14-day retention). The backup stops `bowl-web` briefly while tarring SQLite files.

---

## 10. Migration checklist from PythonAnywhere (summary)

Detailed cutover steps: **section 11**.

1. Copy live data and secrets from PA → VPS (§11.2–11.3).
2. Build VPS stack (sections 2–6), test in parallel (§11.4).
3. Cut DNS (§11.5), move Discord bot (§11.6), repoint deploy (§11.7).
4. Decommission PA when stable (§11.9).

---

## 11. PythonAnywhere → VPS cutover (runbook)

This section turns the current **bowlhockey.com on PythonAnywhere** setup into the nginx + gunicorn VPS layout without changing URLs (`/`, `/bowl-fantasy/`, etc.) or stat/import logic.

### 11.1 What lives on PythonAnywhere today

Typical production layout (defaults in `scripts/STEP2_pythonanywhere.py` and [scripts/README.md](../scripts/README.md)):

| Item | PythonAnywhere path |
| ---- | ------------------- |
| Project root | `/home/BoiledEgg1974/boys-of-winter-hockey-website` |
| Virtualenv | `/home/BoiledEgg1974/venv` |
| WSGI reload file | `/var/www/www_bowlhockey_com_wsgi.py` |
| SSH | `ssh.pythonanywhere.com`, user `BoiledEgg1974` |
| League SQLite | `instance/bowl-historical.db`, `bowl-fantasy.db`, `bowl-cap.db` (+ racing if used) |
| Site DB | `instance/site_membership.db` **or** `SITE_DATABASE_URL` → PA MySQL |
| JSON cache | `instance/league_json_cache/` |
| CSV exports | `data/imports/raw/bowl_historical/`, `bowl_fantasy/`, `bowl_cap/` |
| Secrets / env | `boys-of-winter-hockey-website/.env` (Web tab vars should match this file) |
| Discord bot | **Always-on task:** `python -m scripts.league_discord_bot` (see [DISCORD_BOT_SETUP.md](DISCORD_BOT_SETUP.md)) |

Nightly updates from your PC use **`python scripts/BOWL-Site-Update.py`**, which runs local imports then **`STEP2_pythonanywhere.py deploy-db`**: SSH to the **VPS** (defaults in **`scripts/deploy-live-vps.env.example`**), **capture live OVR / trade log / editorial state**, merge into local DBs, upload SQLite + static, run **`notify_discord_after_db_deploy.py`**, **`systemctl restart bowl-web`**.

The VPS target keeps the **same repo** and **`wsgi:application`**; only the process manager and deploy reload step change (systemd instead of `touch …wsgi.py`).

### 11.2 Phase A — Build the VPS from a PA snapshot (one time)

Do this **before** DNS cutover so the VPS is a faithful copy of live.

**On your PC** (Git Bash or WSL; install OpenSSH). Set key-based SSH to both hosts (`PA_SSH_KEY` as you use for STEP2).

```bash
PA_USER=BoiledEgg1974
PA_HOST=ssh.pythonanywhere.com
PA_ROOT=/home/BoiledEgg1974/boys-of-winter-hockey-website
VPS_USER=bowl
VPS_HOST=YOUR.VPS.IP
VPS_ROOT=/srv/bowl/app
```

1. **Code** — on the VPS (section 3): `git clone` the same branch you deploy on PA, `pip install -r requirements.txt gunicorn`.

2. **Databases + cache** — pull from PA (does not delete PA data):

```bash
rsync -avz -e "ssh -i $PA_SSH_KEY" \
  "$PA_USER@$PA_HOST:$PA_ROOT/instance/" \
  "$VPS_USER@$VPS_HOST:$VPS_ROOT/instance/"

rsync -avz -e "ssh -i $PA_SSH_KEY" \
  "$PA_USER@$PA_HOST:$PA_ROOT/data/imports/raw/" \
  "$VPS_USER@$VPS_HOST:$VPS_ROOT/data/imports/raw/"
```

Include `instance/league_json_cache/` in the first rsync if present (faster first homepage load).

3. **Static assets** (logos, headshots, CSS you did not git-track):

```bash
rsync -avz -e "ssh -i $PA_SSH_KEY" \
  "$PA_USER@$PA_HOST:$PA_ROOT/app/static/" \
  "$VPS_USER@$VPS_HOST:$VPS_ROOT/app/static/"
```

4. **Environment file** — copy PA `.env` to the VPS and edit only what must change:

```bash
scp -i "$PA_SSH_KEY" \
  "$PA_USER@$PA_HOST:$PA_ROOT/.env" \
  "$VPS_USER@$VPS_HOST:$VPS_ROOT/.env"
```

On the VPS `.env`, set at minimum:

- `FLASK_DEBUG=0`
- `SITE_PUBLIC_BASE_URL=https://www.bowlhockey.com` (unchanged until you use a staging hostname)
- `LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1`
- Optional: `HOMEPAGE_SSR_LEADERS_STANDINGS=1`

Keep **`SECRET_KEY`**, **`DISCORD_EVENTS_SHARED_SECRET`**, mail settings, and **`SITE_DATABASE_URL`** the same as PA if you are not moving the site DB yet.

5. Finish sections **5–6** (systemd + nginx). Use a **staging** `server_name` or test via `/etc/hosts` (§11.4) before pointing production DNS.

### 11.3 Site database (MySQL vs SQLite)

| Situation | Action |
| --------- | ------ |
| PA uses **MySQL** (`SITE_DATABASE_URL` in `.env`) | Easiest cutover: **keep the same MySQL URL** from the VPS (PA MySQL allows remote if whitelisted; or migrate DB to VPS MySQL). |
| PA uses **SQLite** `instance/site_membership.db` | Rsync copied it in §11.2; no extra step. |
| Move MySQL **onto the VPS** | One-time: [`.env.example`](../.env.example) → `migrate_site_sqlite_to_mysql.py` / your host’s MySQL docs; then point `SITE_DATABASE_URL` at the new server and re-test GM login, news, Discord queue. |

League stats stay in **per-league SQLite** under `instance/`; only the **site** DB holds GM accounts, news queue, Discord integration, etc.

### 11.4 Phase B — Parallel test (no DNS change yet)

1. On your PC, map the VPS IP to a fake name, e.g. `104.x.x.x staging.bowlhockey.com` in `C:\Windows\System32\drivers\etc\hosts`.
2. Temporarily set nginx `server_name staging.bowlhockey.com;` and obtain TLS, **or** test over HTTP on the IP with `curl -H "Host: www.bowlhockey.com" http://VPS_IP/bowl-fantasy/`.
3. Smoke-test the same list as [UPDATE-NESTED-SERVER.md](UPDATE-NESTED-SERVER.md) §9: hub, each league home, standings, player search, GM login, admin Discord test event.
4. Compare one heavy page (e.g. team page, homepage after cache warm) against PA.

Fix nginx paths, `.env`, and worker count before cutover.

### 11.5 Phase C — Production cutover (DNS)

Namecheap step-by-step: **§13**.

1. **Lower TTL** on `bowlhockey.com` / `www` a day ahead (e.g. 300s) if your DNS provider allows.
2. **Stop sending users to PA:** update **A/AAAA** for `www.bowlhockey.com` (and apex if used) to the **VPS**.
3. Run **certbot** on the VPS for the real `server_name` (section 6).
4. **Restart web** and run cache warm (section 7).
5. Smoke-test from a network **without** `/etc/hosts` overrides.
6. Leave the PA web app **enabled but unused** for 24–48h (rollback, §11.10).

**Discord interactions URL** stays `https://www.bowlhockey.com/api/discord/interactions` — no Developer Portal change if the domain is unchanged.

### 11.6 Discord delivery bot (Always-on → systemd)

1. **Before cutover:** note PA Always-on command (usually):

   `cd /home/BoiledEgg1974/boys-of-winter-hockey-website && /home/BoiledEgg1974/venv/bin/python -m scripts.league_discord_bot`

2. On the VPS, create `/etc/systemd/system/bowl-discord-bot.service`:

```ini
[Unit]
Description=BOWL league Discord delivery bot
After=network.target bowl-web.service

[Service]
User=bowl
Group=bowl
WorkingDirectory=/srv/bowl/app
EnvironmentFile=/srv/bowl/app/.env
Environment=PYTHONPATH=/srv/bowl/app
ExecStart=/srv/bowl/app/.venv/bin/python -m scripts.league_discord_bot
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

3. `sudo systemctl enable --now bowl-discord-bot`.
4. Admin → Discord integration → **Queue test event** on each league.
5. **After the bot is healthy on the VPS**, disable the PA Always-on task (only one poller should run).

Ensure `.env` on the VPS includes `DISCORD_BOT_TOKEN`, `DISCORD_EVENTS_SHARED_SECRET`, and `SITE_PUBLIC_BASE_URL` (same as web).

### 11.7 Phase D — Nightly deploy (VPS is the default)

**`scripts/deploy-live-vps.env.example`** (copy to **`deploy-live-vps.env`**, gitignored) is loaded automatically by **`BOWL-Site-Update.py`**, **`run_site_update.py`**, **`STEP2_pythonanywhere.py`**, and **`BOWL-Site-Update.ps1`**. No extra flags for a normal night:

```powershell
python scripts/BOWL-Site-Update.py
```

STEP2 runs **`deploy-db`** against **`159.203.6.136`** / **`/srv/bowl/app`**, then **`systemctl restart bowl-web`**. Override for legacy PythonAnywhere: **`BOWL_DEPLOY_TARGET=pa`** and **`PA_HOST=ssh.pythonanywhere.com`** in the shell.

**sudo:** allow passwordless restart for `bowl`:

`/etc/sudoers.d/bowl-web`: `bowl ALL=(ALL) NOPASSWD: /bin/systemctl restart bowl-web`

**What still runs on the server during `deploy-db`:** capture live OVR/trade/editorial → merge locally → upload SQLite → `notify_discord_after_db_deploy.py`. That logic is unchanged; it now runs against the VPS tree under `/srv/bowl/app`.

**Code-only changes** (no DB upload): from your PC, `python scripts/sync_vps_app_code.py --pip --restart` (recommended when the droplet is not a git checkout). If `/srv/bowl/app` is a clone: `git pull` + `systemctl restart bowl-web`.

### 11.8 Manual recovery bash (DO console / SSH)

DigitalOcean does **not** offer a PythonAnywhere-style “run this bash on the server” button. Use:

- **Droplet → Access → Launch Droplet Console** (browser shell, usually **root**), or  
- **SSH** from your PC, e.g. `ssh -i ~/.ssh/id_ed25519_pa root@159.203.6.136`

Production paths: app **`/srv/bowl/app`**, venv **`/srv/bowl/app/.venv`**, reload **`systemctl restart bowl-web`** (not `/var/www/…_wsgi.py`). Discord: **`bowl-discord-bot.service`**.

The old PythonAnywhere block (`/home/BoiledEgg1974/…`, `touch www_bowlhockey_com_wsgi.py`) is **not** valid on the VPS.

**Normal night (your PC — includes live DBs + Discord):**

```powershell
python scripts/BOWL-Site-Update.py
```

**Rare full remote rebuild (your PC — equivalent to old PA hard reset + new venv):**

```powershell
python scripts/STEP2_pythonanywhere.py deploy --full-remote-rebuild
```

**Code sync from your PC** (rsync snapshot; skips `.env` / `instance/*.db`):

```powershell
python scripts/sync_vps_app_code.py --pip --restart
```

**One-time:** turn a rsync tree into a clone (keeps `.env`, DBs, `.venv`):

```bash
bash /srv/bowl/app/deploy/vps/init-app-git-checkout.sh
```

**On the droplet** (git checkout at `/srv/bowl/app`, branch `master`):

```bash
cd /srv/bowl/app
git fetch origin
git checkout master
git reset --hard origin/master
source /srv/bowl/app/.venv/bin/activate
pip install --upgrade -r requirements.txt
python -c "import flask, flask_login, flask_sqlalchemy, flask_wtf, pymysql; print('imports ok')"
# Guild id is in /srv/bowl/app/.env — do not blank DISCORD_GUILD_ID unless intentional:
python -m scripts.league_discord_bot.register_slash_commands
python scripts/backup_all_live_data.py
sudo systemctl restart bowl-web bowl-discord-bot
```

That checklist updates **code and dependencies** only. It does **not** import FHM CSVs or upload league SQLite; use **`BOWL-Site-Update`** / **`deploy-db`** for live scores and Discord boxscore queues. See also **`scripts/README.md`** (DigitalOcean VPS bash).

### 11.9 Decommission PythonAnywhere

When the VPS has been stable through at least one full **BOWL-Site-Update** cycle:

1. Disable PA **Web** app and **Always-on** task.
2. Keep a final backup: `python scripts/backup_all_live_data.py` on PA or rsync `instance/` once more.
3. Optionally downgrade/cancel PA hosting.

Do **not** delete PA until league SQLite and site DB backups are verified on the VPS.

### 11.10 Rollback

If something fails right after DNS change:

1. Point DNS A/AAAA back to PythonAnywhere.
2. Re-enable PA Web + Always-on bot; disable VPS bot/web if they might conflict.
3. If you uploaded DBs to the VPS during a bad deploy, restore PA from your last good `instance/` backup — PA was unchanged during parallel testing if you did not run deploy-db against it after cutover.

### 11.11 Cutover timeline (example)

| Day | Action |
| --- | ------ |
| D−3 | Provision VPS, sections 2–6, rsync from PA (§11.2) |
| D−2 | Parallel testing (§11.4), fix nginx/env |
| D−1 | Lower DNS TTL; dry-run `deploy-db --skip-reload` to VPS |
| D0 | DNS to VPS (§11.5); start VPS Discord bot; disable PA bot |
| D+1 | Full `BOWL-Site-Update` with VPS STEP2 (§11.7) |
| D+7 | Decommission PA (§11.9) |

---

## 12. DigitalOcean Droplet created — first-hour checklist

Use this right after you create a Droplet (e.g. **Basic 4 GB / 2 vCPU**, **Ubuntu 24.04**, **nyc1**). Replace `YOUR.DROPLET.IP` and SSH user (`root` on first login, then **`bowl`** once created).

### 12.1 On the Droplet (SSH as root)

```bash
# 1 — Base packages
apt update && apt install -y python3 python3-venv python3-pip nginx git ufw certbot python3-certbot-nginx

# 2 — App user + directory
adduser --disabled-password --gecos "" bowl
mkdir -p /srv/bowl && chown bowl:bowl /srv/bowl

# 3 — Clone app (use your real repo URL)
sudo -u bowl git clone https://github.com/YOUR_ORG/Boys-Of-Winter-League.git /srv/bowl/app
cd /srv/bowl/app && sudo -u bowl python3 -m venv .venv
sudo -u bowl .venv/bin/pip install -r requirements.txt gunicorn

# 4 — Firewall (SSH first, then web)
ufw allow OpenSSH && ufw allow 'Nginx Full' && ufw --force enable

# 5 — Copy .env from PA later (§11.2); for now:
sudo -u bowl cp .env.example .env
# edit: FLASK_DEBUG=0, SECRET_KEY, SITE_PUBLIC_BASE_URL, LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1
```

Install **systemd** + **nginx** from **sections 5–6** (paths `/srv/bowl/app`). Do **not** point production DNS yet.

### 12.2 One-time data copy from PythonAnywhere (your PC)

```bash
# 6 — League DBs, site DB, JSON cache (Git Bash / WSL; set PA_SSH_KEY)
rsync -avz -e "ssh -i $PA_SSH_KEY" BoiledEgg1974@ssh.pythonanywhere.com:/home/BoiledEgg1974/boys-of-winter-hockey-website/instance/ bowl@YOUR.DROPLET.IP:/srv/bowl/app/instance/

# 7 — CSV + static
rsync -avz -e "ssh -i $PA_SSH_KEY" BoiledEgg1974@ssh.pythonanywhere.com:/home/BoiledEgg1974/boys-of-winter-hockey-website/data/imports/raw/ bowl@YOUR.DROPLET.IP:/srv/bowl/app/data/imports/raw/
rsync -avz -e "ssh -i $PA_SSH_KEY" BoiledEgg1974@ssh.pythonanywhere.com:/home/BoiledEgg1974/boys-of-winter-hockey-website/app/static/ bowl@YOUR.DROPLET.IP:/srv/bowl/app/app/static/

# 8 — Production .env (secrets)
scp -i "$PA_SSH_KEY" BoiledEgg1974@ssh.pythonanywhere.com:/home/BoiledEgg1974/boys-of-winter-hockey-website/.env bowl@YOUR.DROPLET.IP:/srv/bowl/app/.env
```

On the VPS, edit `.env`: set `LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1` (and optional `HOMEPAGE_SSR_LEADERS_STANDINGS=1`).

### 12.3 Start web + smoke test (before Namecheap cutover)

```bash
# 9 — On VPS
sudo systemctl enable --now bowl-web
sudo nginx -t && sudo systemctl reload nginx

# 10 — From your PC (replace IP; simulates Host header until DNS moves)
curl -sI -H "Host: www.bowlhockey.com" http://YOUR.DROPLET.IP/bowl-fantasy/ | head -5
```

Test in a browser via **`/etc/hosts`** (§11.4): map `YOUR.DROPLET.IP` → `www.bowlhockey.com`. When happy, follow **§13** for Namecheap DNS, then **§11.5–11.7** (cutover, Discord bot, deploy-db to VPS).

---

## 13. Namecheap — domain, DNS, and cutover from PythonAnywhere

You usually **keep the domain registered at Namecheap** and only **change DNS** so `bowlhockey.com` / `www` point to the DigitalOcean Droplet. You do **not** need to transfer the domain to DigitalOcean unless you want Namecheap purely as registrar and DO to host DNS (optional, §13.3).

### 13.1 What stays where

| Piece | Where it lives |
| ----- | -------------- |
| Domain registration | **Namecheap** (renewal, WHOIS, domain lock) |
| Website + app | **DigitalOcean Droplet** (nginx + gunicorn) |
| Old host (during migration) | **PythonAnywhere** until you disable it after cutover |
| Discord Developer Portal | **No change** if the public URL stays `https://www.bowlhockey.com/...` |

### 13.2 Find your Droplet IP

DigitalOcean → **Droplets** → your server → copy **Public IPv4** (e.g. `157.x.x.x`). You will create DNS records pointing to this address.

### 13.3 Two ways to manage DNS (pick one)

**Option A — Keep DNS at Namecheap (simplest for most)**

1. Namecheap → **Domain List** → **Manage** next to `bowlhockey.com`.
2. **Advanced DNS** tab.
3. Remove or edit old records that pointed to **PythonAnywhere** (often an **A** record for `@` and/or **CNAME** for `www` to PA or parking).
4. Add/update:

| Type | Host | Value | TTL |
| ---- | ---- | ----- | --- |
| **A** | `@` | `YOUR.DROPLET.IP` | 300 (before cutover) → 3600 after stable |
| **A** | `www` | `YOUR.DROPLET.IP` | same |

Some setups use **CNAME** `www` → `@`; Namecheap supports that if `@` is an A record to the Droplet IP.

**Option B — Use DigitalOcean nameservers**

1. DO → **Networking** → **Domains** → **Add Domain** → `bowlhockey.com`.
2. Create the same **A** records (`@` and `www` → Droplet IP) in DO.
3. Namecheap → **Domain** → **Nameservers** → **Custom DNS** → set to DO’s nameservers (e.g. `ns1.digitalocean.com`, `ns2.digitalocean.com`, `ns3.digitalocean.com`).
4. Propagation can take up to 24–48 hours; until then, either nameserver set is “live.”

For a single site, **Option A** is enough.

### 13.4 Before you change DNS (reduce downtime)

1. **Lower TTL** on existing `@` / `www` records to **300 seconds** (5 min) at Namecheap **24–48 hours before** cutover.
2. Finish **§12** and **§11.4** parallel testing (hosts file or staging).
3. On the VPS, ensure nginx `server_name` includes **`www.bowlhockey.com`** and **`bowlhockey.com`** (section 6).
4. Obtain TLS **after** DNS points to the Droplet (or use hosts-file testing only on HTTP until cutover):

```bash
sudo certbot --nginx -d www.bowlhockey.com -d bowlhockey.com
```

Certbot needs the world to resolve the name to your Droplet for the HTTP challenge (unless you use DNS challenge — not covered here).

### 13.5 Cutover day (Namecheap + DO)

1. Update **A** records (`@` and `www`) to the **Droplet IP** (§13.3).
2. Wait for TTL (often 5–30 minutes if you lowered TTL).
3. Verify:

```bash
nslookup www.bowlhockey.com
curl -sI https://www.bowlhockey.com/bowl-fantasy/
```

4. Run **cache warm** (section 7).
5. Start **Discord bot** on VPS (§11.6); disable PA Always-on.
6. **`SITE_PUBLIC_BASE_URL`** in `.env` must stay **`https://www.bowlhockey.com`** (no trailing slash).

### 13.6 Do not break email (if you use Namecheap mail)

If `@` uses Namecheap **Email Forwarding** or **Private Email**, **do not delete MX records** when editing Advanced DNS. Typical pattern:

- Leave **MX** (and any **TXT** for SPF/DKIM) untouched.
- Only change **A** / **CNAME** for the **website** hostnames (`@`, `www`).

If you are unsure, screenshot Advanced DNS before editing.

### 13.7 Apex vs www (redirect)

Browsers and Discord links may use either `bowlhockey.com` or `www.bowlhockey.com`. Best practice:

- **A** records for **both** `@` and `www` to the Droplet, **or** `@` A + `www` CNAME to `@`.
- In nginx, one `server` block with both names (section 6) and certbot for both `-d` flags.

Your Flask app expects the same paths on both; hub is at `/`.

### 13.8 PythonAnywhere after DNS moves

Traffic goes to DO; PA is idle unless someone uses the old IP directly.

1. Keep PA **read-only** for a few days (rollback §11.10).
2. Repoint **`deploy-db`** to the VPS (§11.7).
3. Disable PA web + Always-on when stable (§11.9).

You do **not** need to “transfer the domain” away from Namecheap for this migration.

### 13.9 Optional: move only DNS hosting to DigitalOcean

Registrar stays Namecheap; nameservers switch to DO (§13.3 Option B). Useful if you later add DO **Load Balancers** or multiple Droplets. Not required for one Droplet.

### 13.10 Troubleshooting

| Symptom | Check |
| ------- | ----- |
| Site still opens on PA | DNS cache / old TTL; `nslookup` from phone off Wi‑Fi; flush local DNS |
| Certificate fails | A record not pointing to Droplet yet; wait propagation |
| `www` works, apex does not | Missing **A** for `@` or wrong IP |
| Mixed content / wrong league paths | nginx must proxy full path; see section 6 |
| Email stopped | MX records changed by mistake — restore from screenshot |

---

## Quick reference

| Component | Value |
| --------- | ----- |
| WSGI entry | `wsgi:application` |
| Working directory | project root (contains `wsgi.py`) |
| JSON cache dir | `instance/league_json_cache/` |
| League DBs | `instance/bowl-historical.db`, `bowl-fantasy.db`, `bowl-cap.db` |
| Site DB | `instance/site_membership.db` or `SITE_DATABASE_URL` |
