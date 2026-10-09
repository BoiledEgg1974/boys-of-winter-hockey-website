# VPS migration helpers (DigitalOcean)

Droplet **159.203.6.136** (Toronto). Full guide: [docs/DEPLOY-VPS-NGINX.md](../../docs/DEPLOY-VPS-NGINX.md).

## If SSH times out (UFW / firewall)

1. DigitalOcean → Droplet → **Access** → **Launch Droplet Console**.
2. Log in as **root**.
3. Run:
   ```bash
   ufw allow OpenSSH
   ufw allow 'Nginx Full'
   ufw status
   ```
   Or temporarily: `ufw disable` (re-enable after confirming rules).

Also check **Networking → Firewalls** in DO — allow inbound **22**, **80**, **443**.

## Start data transfer (PA → Droplet)

On the droplet as **root** (after `id_ed25519_pa` is in `/root/.ssh/` with mode 600):

```bash
sed -i 's/\r$//' /root/pull-from-pythonanywhere.sh
nohup bash /root/pull-from-pythonanywhere.sh &
tail -f /var/log/bowl-rsync.log
```

Wait for `/var/log/bowl-rsync.done` (~6–8 GB, may take 30–90+ minutes).

## After rsync completes

```bash
sed -i 's/\r$//' /root/post-sync-setup.sh
bash /root/post-sync-setup.sh
```

Upload latest scripts from your PC if needed:

```powershell
scp -i $env:USERPROFILE\.ssh\id_ed25519_pa deploy/vps/*.sh deploy/vps/*.service deploy/vps/nginx-bowl*.conf root@159.203.6.136:/root/
```

Production HTTPS uses `nginx-bowl-live-443.conf` plus `nginx-bowl-snippets-{gzip,static}.conf` under `/etc/nginx/snippets/` (static assets bypass gunicorn).

After each `systemctl restart bowl-web`, **`bowl-cache-warm.service`** runs `deploy/vps/warm-league-json-cache.sh` (three homepage summary APIs). Logs: `journalctl -u bowl-cache-warm -n 20`.

## Code sync (PC → VPS)

After you pull on your PC, either rsync code:

```powershell
python scripts/sync_vps_app_code.py --pip --restart
```

or on the VPS (git clone at `/srv/bowl/app`):

```bash
cd /srv/bowl/app && git fetch origin && git reset --hard origin/master
```

First-time git on a rsync-only tree: `bash deploy/vps/init-app-git-checkout.sh` (as root on the droplet).

## Nightly backups

On the droplet as **root** (after code sync):

```bash
bash /srv/bowl/app/deploy/vps/install-backup-timer.sh
systemctl start bowl-backup.service   # optional test (~1 min site pause for SQLite)
```

Artifacts: `/srv/bowl/backups/mysql/`, `instance/`, retained **14 days** (timer **07:00 UTC** daily).

## Offsite copies

**PC (now):** `python scripts/pull_vps_backups.py` → `backup-from-do/vps-backups/`

**DigitalOcean Spaces (recommended):** create a Space in **tor1**, copy `deploy/vps/offsite-backup.env.example` to **`scripts/offsite-backup.env`** (gitignored), fill access key + secret, then:

```powershell
python scripts/push_offsite_backup_env.py --test-sync
```

VPS timer **`bowl-backup-offsite.timer`** runs daily **08:45 UTC** (after local backup).

**DR (no NYC3 required):** `bowl-hockey-backups` in **tor1** holds live offsite copies; set `BOWL_SPACES_COPY_PREFIX=bowl-production-dr` for a second prefix in the same Space; **`bowl-backup-offsite-secondary.service`** runs after each primary sync. **PC copy:** `python scripts/mirror_spaces_backups_local.py` → `backup-from-do/spaces-mirror/primary/`. Optional NYC3: set `BOWL_SPACES_SECONDARY_*` only if you create a Space there.

**Checks:** `python scripts/verify_spaces_backups.py` · **intra copy:** `python scripts/sync_spaces_intra_copy.py`

## Site MySQL (502 fix)

PythonAnywhere MySQL **does not accept connections from the droplet** (timeout). League SQLite + Perfect Squad on disk are fine; **gunicorn exits** until `SITE_DATABASE_URL` points at a DB the VPS can reach.

1. On **PA** (already done once):  
   `python scripts/backup_all_live_data.py --out ~/site-migration-backup --no-prune`  
   → live rows in `~/site-migration-backup/site/tables/*.json`.

2. On the **droplet** (needs `import_site_tables_json_to_mysql.py` in `/srv/bowl/app` — scp from PC if missing):

   ```bash
   export BOWL_SITE_MYSQL_PASSWORD='your-long-random-password'
   sed -i 's/\r$//' /root/setup-site-mysql-from-pa-backup.sh
   bash /root/setup-site-mysql-from-pa-backup.sh
   ```

   Or DO Managed MySQL: create DB, set `SITE_DATABASE_URL`, rsync `site/tables/` from PA, run import with `--force`.

3. `systemctl restart bowl-web` — expect **200** on  
   `curl -H 'Host: www.bowlhockey.com' http://127.0.0.1/bowl-fantasy/`

Do **not** cut DNS until GM login / news / Discord queue look right.

## DNS

Do **not** switch Namecheap until `curl -H 'Host: www.bowlhockey.com' http://159.203.6.136/bowl-fantasy/` looks good. Then §13 in the main doc.

## Security after migration

Remove `/root/.ssh/id_ed25519_pa` from the droplet when rsync is done.
