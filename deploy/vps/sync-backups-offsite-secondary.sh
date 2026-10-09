#!/usr/bin/env bash
# DR copy: second region bucket and/or duplicate prefix in the same Space.
set -euo pipefail

ENV_FILE="${BOWL_OFFSITE_ENV:-/etc/bowl/offsite-backup.env}"
RCLONE_CONF="${BOWL_RCLONE_CONF:-/etc/bowl/rclone.conf}"
BACKUP_ROOT="${BOWL_BACKUP_ROOT:-/srv/bowl/backups}"
LOG_DIR="${BACKUP_ROOT}/logs"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="${LOG_DIR}/offsite-secondary-${STAMP}.log"

mkdir -p "$LOG_DIR"
exec >>"$LOG" 2>&1

log() { echo "[$(date -Is)] $*"; }

if [[ ! -f "$ENV_FILE" ]]; then
  log "skip: missing $ENV_FILE"
  exit 0
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

if ! command -v rclone >/dev/null 2>&1; then
  log "ERROR: rclone not installed"
  exit 1
fi

PREFIX="${BOWL_SPACES_PREFIX:-bowl-production}"
PRIMARY="${BOWL_SPACES_BUCKET:?}"
SRC="bowl-spaces:${PRIMARY}/${PREFIX}/backups"

if [[ -n "${BOWL_SPACES_SECONDARY_ENDPOINT:-}" && -n "${BOWL_SPACES_SECONDARY_BUCKET:-}" ]]; then
  DST="bowl-spaces-dr:${BOWL_SPACES_SECONDARY_BUCKET}/${PREFIX}/backups"
  log "cross-region mirror ${SRC} -> ${DST}"
  rclone sync "$SRC" "$DST" \
    --config "$RCLONE_CONF" \
    --fast-list \
    --transfers 4 \
    --checkers 8 \
    --s3-no-check-bucket
  log "cross-region mirror done"
fi

if [[ -n "${BOWL_SPACES_COPY_PREFIX:-}" ]]; then
  COPY_DST="bowl-spaces:${PRIMARY}/${BOWL_SPACES_COPY_PREFIX}/backups"
  log "intra-Space copy ${SRC} -> ${COPY_DST}"
  rclone sync "$SRC" "$COPY_DST" \
    --config "$RCLONE_CONF" \
    --fast-list \
    --transfers 4 \
    --checkers 8 \
    --s3-no-check-bucket
  log "intra-Space copy done"
fi

if [[ -z "${BOWL_SPACES_SECONDARY_ENDPOINT:-}${BOWL_SPACES_SECONDARY_BUCKET:-}${BOWL_SPACES_COPY_PREFIX:-}" ]]; then
  log "skip: no DR target (set COPY_PREFIX and/or secondary region in $ENV_FILE)"
fi
