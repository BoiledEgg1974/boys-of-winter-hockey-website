#!/usr/bin/env bash
# Nightly MariaDB + league SQLite backup on the VPS (systemd timer).
set -euo pipefail

APP="${BOWL_APP_ROOT:-/srv/bowl/app}"
BACKUP_ROOT="${BOWL_BACKUP_ROOT:-/srv/bowl/backups}"
KEEP_DAYS="${BOWL_BACKUP_KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG_DIR="${BACKUP_ROOT}/logs"
MYSQL_DIR="${BACKUP_ROOT}/mysql"
INSTANCE_DIR="${BACKUP_ROOT}/instance"

mkdir -p "$LOG_DIR" "$MYSQL_DIR" "$INSTANCE_DIR"
LOG="${LOG_DIR}/backup-${STAMP}.log"
exec >>"$LOG" 2>&1

log() { echo "[$(date -Is)] $*"; }

log "backup start (keep ${KEEP_DAYS} days)"

if command -v mariadb-dump >/dev/null 2>&1; then
  DUMP=mariadb-dump
elif command -v mysqldump >/dev/null 2>&1; then
  DUMP=mysqldump
else
  log "ERROR: no mariadb-dump/mysqldump"
  exit 1
fi

DB_NAME="${BOWL_SITE_MYSQL_DATABASE:-bowlsite}"
OUT_SQL="${MYSQL_DIR}/bowlsite-${STAMP}.sql.gz"
if "$DUMP" --single-transaction --routines --triggers "$DB_NAME" | gzip -9 >"$OUT_SQL"; then
  log "mysql ok: $(du -h "$OUT_SQL" | awk '{print $1}')"
else
  log "ERROR: mysql dump failed"
  exit 1
fi

log "stopping bowl-web for consistent SQLite snapshot"
systemctl stop bowl-web
trap 'systemctl start bowl-web; log "bowl-web restarted"' EXIT

mapfile -t DB_FILES < <(find "$APP/instance" -maxdepth 1 -type f -name '*.db' | sort)
if ((${#DB_FILES[@]} == 0)); then
  log "WARN: no *.db under $APP/instance"
else
  TAR="${INSTANCE_DIR}/league-sqlite-${STAMP}.tar.gz"
  tar -czf "$TAR" -C "$APP/instance" $(basename -a "${DB_FILES[@]}")
  log "sqlite ok: $(du -h "$TAR" | awk '{print $1}') (${#DB_FILES[@]} files)"
fi

find "$MYSQL_DIR" -type f -name 'bowlsite-*.sql.gz' -mtime +"$KEEP_DAYS" -delete
find "$INSTANCE_DIR" -type f -name 'league-sqlite-*.tar.gz' -mtime +"$KEEP_DAYS" -delete
find "$LOG_DIR" -type f -name 'backup-*.log' -mtime +"$((KEEP_DAYS * 2))" -delete

log "backup done"
