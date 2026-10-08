#!/usr/bin/env bash
# Run on the Droplet as root AFTER /var/log/bowl-rsync.done exists.
set -euo pipefail
APP=/srv/bowl/app
PS=/srv/bowl/perfect-squad

if [[ ! -f /var/log/bowl-rsync.done ]]; then
  echo "Wait for rsync: tail -f /var/log/bowl-rsync.log" >&2
  exit 1
fi

# Paths for Perfect Squad (were PythonAnywhere home dirs in .env).
if [[ -f "$APP/.env" ]]; then
  sed -i 's|^PERFECT_SQUAD_ROOT=.*|PERFECT_SQUAD_ROOT=/srv/bowl/perfect-squad|' "$APP/.env"
  sed -i 's|^PERFECT_SQUAD_DATABASE_URL=.*|PERFECT_SQUAD_DATABASE_URL=sqlite:////srv/bowl/perfect-squad/instance/perfect-squad.db|' "$APP/.env"
  grep -q '^LEAGUE_JSON_CACHE_WARM_ON_STARTUP=' "$APP/.env" \
    && sed -i 's/^LEAGUE_JSON_CACHE_WARM_ON_STARTUP=.*/LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1/' "$APP/.env" \
    || echo 'LEAGUE_JSON_CACHE_WARM_ON_STARTUP=1' >> "$APP/.env"
  grep -q '^HOMEPAGE_SSR_LEADERS_STANDINGS=' "$APP/.env" \
    && sed -i 's/^HOMEPAGE_SSR_LEADERS_STANDINGS=.*/HOMEPAGE_SSR_LEADERS_STANDINGS=1/' "$APP/.env" \
    || echo 'HOMEPAGE_SSR_LEADERS_STANDINGS=1' >> "$APP/.env"
  sed -i 's/^FLASK_DEBUG=.*/FLASK_DEBUG=0/' "$APP/.env" || true
fi

sudo -u bowl python3 -m venv "$APP/.venv"
sudo -u bowl "$APP/.venv/bin/pip" install -q -r "$APP/requirements.txt" gunicorn
if [[ -f "$PS/requirements.txt" ]]; then
  sudo -u bowl "$APP/.venv/bin/pip" install -q -r "$PS/requirements.txt"
fi

install -d -o bowl -g bowl "$APP/deploy/vps"
install -m 755 /root/warm-league-json-cache.sh "$APP/deploy/vps/warm-league-json-cache.sh"
install -m 644 /root/bowl-web.service /etc/systemd/system/bowl-web.service
install -m 644 /root/bowl-cache-warm.service /etc/systemd/system/bowl-cache-warm.service
install -m 644 /root/bowl-discord-bot.service /etc/systemd/system/bowl-discord-bot.service
install -m 644 /root/nginx-bowl-snippets-gzip.conf /etc/nginx/snippets/bowl-gzip.conf
install -m 644 /root/nginx-bowl-snippets-static.conf /etc/nginx/snippets/bowl-static.conf
if [ -f /root/nginx-bowl-live-443.conf ]; then
  install -m 644 /root/nginx-bowl-live-443.conf /etc/nginx/sites-available/bowl
else
  install -m 644 /root/nginx-bowl.conf /etc/nginx/sites-available/bowl
fi
ln -sf /etc/nginx/sites-available/bowl /etc/nginx/sites-enabled/bowl
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl daemon-reload
systemctl enable bowl-web
systemctl restart bowl-web
systemctl reload nginx

echo "Web stack up. Test: curl -sI -H 'Host: www.bowlhockey.com' http://127.0.0.1/bowl-fantasy/"
echo "MySQL site DB check (must succeed for GM/news/Discord queue):"
sudo -u bowl bash -c "cd $APP && set -a && source .env && set +a && $APP/.venv/bin/python scripts/verify_site_mysql_connection.py" || true

echo "Enable Discord bot after smoke test: systemctl enable --now bowl-discord-bot"
