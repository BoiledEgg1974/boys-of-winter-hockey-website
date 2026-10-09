#!/usr/bin/env bash
# Install nightly backup timer on the droplet (run as root).
set -euo pipefail
APP="${BOWL_APP_ROOT:-/srv/bowl/app}"
if [[ ! -x "$APP/deploy/vps/bowl-nightly-backup.sh" ]]; then
  echo "Missing $APP/deploy/vps/bowl-nightly-backup.sh — sync code first." >&2
  exit 1
fi
chmod 755 "$APP/deploy/vps/bowl-nightly-backup.sh"
install -m 644 "$APP/deploy/vps/bowl-backup.service" /etc/systemd/system/bowl-backup.service
install -m 644 "$APP/deploy/vps/bowl-backup.timer" /etc/systemd/system/bowl-backup.timer
mkdir -p /srv/bowl/backups/{mysql,instance,logs}
systemctl daemon-reload
systemctl enable --now bowl-backup.timer
systemctl list-timers bowl-backup.timer --no-pager
echo "Optional test: systemctl start bowl-backup.service && tail -20 /srv/bowl/backups/logs/backup-*.log"
