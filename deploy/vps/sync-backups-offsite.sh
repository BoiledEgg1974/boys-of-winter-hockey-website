#!/usr/bin/env bash
# Upload /srv/bowl/backups to DigitalOcean Spaces via rclone (S3-compatible).
set -euo pipefail

ENV_FILE="${BOWL_OFFSITE_ENV:-/etc/bowl/offsite-backup.env}"
RCLONE_CONF="${BOWL_RCLONE_CONF:-/etc/bowl/rclone.conf}"
BACKUP_ROOT="${BOWL_BACKUP_ROOT:-/srv/bowl/backups}"
LOG_DIR="${BACKUP_ROOT}/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${LOG_DIR}/offsite-${STAMP}.log"

mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1

log() { echo "[$(date -Is)] $*"; }

if [[ ! -f "$ENV_FILE" ]]; then
  log "skip: missing $ENV_FILE (see deploy/vps/offsite-backup.env.example)"
  exit 0
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

for var in BOWL_SPACES_ENDPOINT BOWL_SPACES_BUCKET BOWL_SPACES_ACCESS_KEY BOWL_SPACES_SECRET_KEY; do
  if [[ -z "${!var:-}" ]]; then
    log "skip: $var is empty in $ENV_FILE"
    exit 0
  fi
done

if ! command -v rclone >/dev/null 2>&1; then
  log "ERROR: rclone not installed"
  exit 1
fi

PREFIX="${BOWL_SPACES_PREFIX:-bowl-production}"
REMOTE="bowl-spaces:${BOWL_SPACES_BUCKET}/${PREFIX}/backups"

log "offsite sync -> ${REMOTE}"
rclone sync "$BACKUP_ROOT/" "$REMOTE" \
  --config "$RCLONE_CONF" \
  --fast-list \
  --transfers 4 \
  --checkers 8 \
  --s3-no-check-bucket \
  --exclude "logs/offsite-*.log"

log "offsite sync done"
