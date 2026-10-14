# Deploy BOWL on a VPS (nginx + gunicorn)

This guide deploys the combined hub + three league sites on a Linux VPS with **nginx** in front and **gunicorn** running `wsgi.application`. The app layout is unchanged: `/`, `/bowl-historical/`, `/bowl-fantasy/`, `/bowl-cap/`.

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

# Site DB — local MariaDB on the VPS (recommended) or SQLite:
# SITE_DATABASE_URL=mysql+pymysql://bowl:PASSWORD@127.0.0.1/bowlsite
```

On a **2 GB** VPS, **`LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1`** (plus **`bowl-cache-warm.service`**) is recommended so homepage JSON is warm after restarts.

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

- **`wsgi:application`** is the combined DispatcherMiddleware app (hub + three leagues).
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

## 10. Production layout on the VPS

| Item | Location |
| ---- | -------- |
| App root | /srv/bowl/app |
| Virtualenv | /srv/bowl/app/.venv |
| Web process | owl-web.service (gunicorn on 127.0.0.1:8000) |
| Discord bot | owl-discord-bot.service |
| League SQLite | instance/bowl-historical.db, owl-fantasy.db, owl-cap.db |
| Site DB | SITE_DATABASE_URL (local MariaDB or instance/site_membership.db) |
| JSON cache | instance/league_json_cache/ |
| Nightly deploy from PC | python scripts/BOWL-Site-Update.py + scripts/deploy-live-vps.env |

Copy scripts/deploy-live-vps.env.example to deploy-live-vps.env (gitignored) for machine-specific SSH paths.

---

## 11. Nightly deploy and recovery (from your PC)

**scripts/deploy-live-vps.env.example** is loaded by **BOWL-Site-Update.py**, **
un_site_update.py**, **STEP2_remote_deploy.py**, and **BOWL-Site-Update.ps1**.

Normal night (local imports, upload league SQLite, Discord notify, restart web):

`powershell
python scripts/BOWL-Site-Update.py
`

STEP2 runs **deploy-db** on the droplet (/srv/bowl/app), then **systemctl restart bowl-web**.

**Code-only** (no DB upload): python scripts/sync_vps_app_code.py --pip --restart, or on the server git pull and restart services.

**Full remote rebuild** (rare): python scripts/STEP2_remote_deploy.py deploy --full-remote-rebuild

**On the droplet:** see scripts/README.md (DigitalOcean VPS bash) for git pull, 
egister_slash_commands, backups, and systemctl restart bowl-web bowl-discord-bot.

**Console / SSH:** Droplet **Access → Launch Droplet Console**, or SSH as 
oot. App **/srv/bowl/app**, web reload **systemctl restart bowl-web**, bot **owl-discord-bot.service**.

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

# 5 — Copy production .env when ready; for now:
sudo -u bowl cp .env.example .env
# edit: FLASK_DEBUG=0, SECRET_KEY, SITE_PUBLIC_BASE_URL, LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1
```

Install **systemd** + **nginx** from **sections 5–6** (paths `/srv/bowl/app`). Do **not** point production DNS yet.

### 12.2 One-time data copy (your PC)

If you are seeding a new droplet from another machine, rsync `instance/`, `data/imports/raw/`, `app/static/`, and `.env` into `/srv/bowl/app/`. The helper script **`deploy/vps/pull-legacy-site-data.sh`** (run on the droplet as root) documents a full-tree pull when you still have SSH access to the old host.

On the VPS, edit `.env`: set `LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1` (and optional `HOMEPAGE_SSR_LEADERS_STANDINGS=1`).

### 12.3 Start web + smoke test (before Namecheap cutover)

```bash
# 9 — On VPS
sudo systemctl enable --now bowl-web
sudo nginx -t && sudo systemctl reload nginx

# 10 — From your PC (replace IP; simulates Host header until DNS moves)
curl -sI -H "Host: www.bowlhockey.com" http://YOUR.DROPLET.IP/bowl-fantasy/ | head -5
```

Test in a browser via **`/etc/hosts`**: map `YOUR.DROPLET.IP` → `www.bowlhockey.com`. When happy, follow **§13** for Namecheap DNS, enable **`bowl-discord-bot`**, and run **`BOWL-Site-Update.py`** against the droplet.

---

## 13. Namecheap — domain and DNS

You usually **keep the domain registered at Namecheap** and only **change DNS** so `bowlhockey.com` / `www` point to the DigitalOcean Droplet. You do **not** need to transfer the domain to DigitalOcean unless you want Namecheap purely as registrar and DO to host DNS (optional, §13.3).

### 13.1 What stays where

| Piece | Where it lives |
| ----- | -------------- |
| Domain registration | **Namecheap** (renewal, WHOIS, domain lock) |
| Website + app | **DigitalOcean Droplet** (nginx + gunicorn) |
| Discord Developer Portal | **No change** if the public URL stays `https://www.bowlhockey.com/...` |

### 13.2 Find your Droplet IP

DigitalOcean → **Droplets** → your server → copy **Public IPv4** (e.g. `157.x.x.x`). You will create DNS records pointing to this address.

### 13.3 Two ways to manage DNS (pick one)

**Option A — Keep DNS at Namecheap (simplest for most)**

1. Namecheap → **Domain List** → **Manage** next to `bowlhockey.com`.
2. **Advanced DNS** tab.
3. Remove or edit stale **A** / **CNAME** records for `@` and `www` that point at an old host or parking page.
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
2. Finish **§12** and parallel testing (hosts file or staging hostname).
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
5. Start **`bowl-discord-bot`** on the VPS (only one poller should run).
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

### 13.8 Optional: move only DNS hosting to DigitalOcean

Registrar stays Namecheap; nameservers switch to DO (§13.3 Option B). Useful if you later add DO **Load Balancers** or multiple Droplets. Not required for one Droplet.

### 13.10 Troubleshooting

| Symptom | Check |
| ------- | ----- |
| Site still opens on the VPS | DNS cache / old TTL; `nslookup` from phone off Wi‑Fi; flush local DNS |
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
