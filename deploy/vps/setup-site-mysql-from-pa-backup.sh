#!/usr/bin/env bash
# Run on the droplet as root after post-sync-setup.sh.
# Pulls live site table JSON from PA (backup already on PA) and loads local MariaDB.
set -euo pipefail

APP=/srv/bowl/app
PA_BACKUP=/home/BoiledEgg1974/site-migration-backup
LOCAL_BACKUP=/srv/bowl/site-migration-backup
TABLES="${LOCAL_BACKUP}/site/tables"

if [[ ! -d "$APP/.venv" ]]; then
  echo "Missing venv at $APP/.venv — run post-sync-setup.sh first." >&2
  exit 1
fi

if ! command -v mariadb >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y mariadb-server
  systemctl enable --now mariadb
fi

if [[ -z "${BOWL_SITE_MYSQL_PASSWORD:-}" ]]; then
  echo "Set BOWL_SITE_MYSQL_PASSWORD before running (strong random password)." >&2
  exit 1
fi

mariadb -e "CREATE DATABASE IF NOT EXISTS bowlsite CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
mariadb -e "CREATE USER IF NOT EXISTS 'bowl'@'localhost' IDENTIFIED BY '${BOWL_SITE_MYSQL_PASSWORD}';"
mariadb -e "ALTER USER 'bowl'@'localhost' IDENTIFIED BY '${BOWL_SITE_MYSQL_PASSWORD}';"
mariadb -e "GRANT ALL PRIVILEGES ON bowlsite.* TO 'bowl'@'localhost'; FLUSH PRIVILEGES;"

mkdir -p "$LOCAL_BACKUP/site"
RSYNC_RSH="ssh -i /root/.ssh/id_ed25519_pa -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
rsync -avz --progress -e "$RSYNC_RSH" \
  "BoiledEgg1974@ssh.pythonanywhere.com:${PA_BACKUP}/site/tables/" \
  "$TABLES/"

ENV_FILE="$APP/.env"
cp -a "$ENV_FILE" "${ENV_FILE}.bak-before-local-mysql"
grep -v '^SITE_DATABASE_URL=' "$ENV_FILE" > "${ENV_FILE}.tmp" || true
mv "${ENV_FILE}.tmp" "$ENV_FILE"
{
  echo "# PythonAnywhere MySQL is not reachable from this VPS — use local MariaDB."
  echo "SITE_DATABASE_URL=mysql+pymysql://bowl:${BOWL_SITE_MYSQL_PASSWORD}@127.0.0.1/bowlsite"
} >> "$ENV_FILE"

cd "$APP"
sudo -u bowl bash -lc "source .venv/bin/activate && python scripts/import_site_tables_json_to_mysql.py '$TABLES' --force"
sudo -u bowl bash -lc "source .venv/bin/activate && python scripts/verify_site_mysql_connection.py"

systemctl restart bowl-web
echo "Smoke test:"
curl -sS -o /dev/null -w '%{http_code}\n' -H 'Host: www.bowlhockey.com' http://127.0.0.1/bowl-fantasy/ || true
