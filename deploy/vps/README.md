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
scp -i $env:USERPROFILE\.ssh\id_ed25519_pa deploy/vps/*.sh deploy/vps/*.service deploy/vps/nginx-bowl.conf root@159.203.6.136:/root/
```

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
