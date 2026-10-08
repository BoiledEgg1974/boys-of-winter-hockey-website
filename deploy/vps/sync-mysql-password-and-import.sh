#!/usr/bin/env bash
set -euo pipefail
python3 <<'PY'
import re, subprocess
from pathlib import Path
from urllib.parse import unquote

text = Path("/srv/bowl/app/.env").read_text()
m = re.search(r"^SITE_DATABASE_URL=mysql\+pymysql://([^:]+):([^@]+)@", text, re.M)
if not m:
    raise SystemExit("SITE_DATABASE_URL missing")
user, pw = m.group(1), unquote(m.group(2))
pw_sql = pw.replace("\\", "\\\\").replace("'", "''")
sql = f"ALTER USER '{user}'@'localhost' IDENTIFIED BY '{pw_sql}'; FLUSH PRIVILEGES;"
subprocess.run(["mariadb", "-e", sql], check=True)
print("mysql user password synced from .env")
PY
sudo -u bowl bash -lc "cd /srv/bowl/app && source .venv/bin/activate && python scripts/import_site_tables_json_to_mysql.py /srv/bowl/site-migration-backup/site/tables --force"
sudo -u bowl bash -lc "cd /srv/bowl/app && source .venv/bin/activate && PYTHONPATH=/srv/bowl/app python scripts/verify_site_mysql_connection.py"
systemctl restart bowl-web
sleep 4
curl -sS -o /dev/null -w "fantasy:%{http_code}\n" -H "Host: www.bowlhockey.com" http://127.0.0.1/bowl-fantasy/
