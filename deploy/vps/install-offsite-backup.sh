#!/usr/bin/env bash
# Install rclone + offsite backup timer (run on droplet as root).
set -euo pipefail

APP="${BOWL_APP_ROOT:-/srv/bowl/app}"
ENV_FILE="/etc/bowl/offsite-backup.env"
RCLONE_CONF="/etc/bowl/rclone.conf"
EXAMPLE="$APP/deploy/vps/offsite-backup.env.example"

apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq rclone

install -d -m 755 /etc/bowl
if [[ ! -f "$ENV_FILE" && -f "$EXAMPLE" ]]; then
  install -m 600 "$EXAMPLE" "$ENV_FILE"
  sed -i 's/\r$//' "$ENV_FILE"
  echo "Created $ENV_FILE — add Spaces access key + secret, then re-run this script."
fi

if [[ -f "$ENV_FILE" ]]; then
  sed -i 's/\r$//' "$ENV_FILE"
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  if [[ -n "${BOWL_SPACES_ACCESS_KEY:-}" && -n "${BOWL_SPACES_SECRET_KEY:-}" ]]; then
    ENDPOINT="${BOWL_SPACES_ENDPOINT:-tor1.digitaloceanspaces.com}"
    umask 077
    {
      printf '%s\n' '[bowl-spaces]' 'type = s3' 'provider = DigitalOcean'
      printf 'access_key_id = %s\n' "$BOWL_SPACES_ACCESS_KEY"
      printf 'secret_access_key = %s\n' "$BOWL_SPACES_SECRET_KEY"
      printf 'endpoint = %s\n' "$ENDPOINT"
      printf '%s\n' 'acl = private'
      if [[ -n "${BOWL_SPACES_SECONDARY_ENDPOINT:-}" && -n "${BOWL_SPACES_SECONDARY_BUCKET:-}" ]]; then
        printf '\n%s\n' '[bowl-spaces-dr]' 'type = s3' 'provider = DigitalOcean'
        printf 'access_key_id = %s\n' "$BOWL_SPACES_ACCESS_KEY"
        printf 'secret_access_key = %s\n' "$BOWL_SPACES_SECRET_KEY"
        printf 'endpoint = %s\n' "$BOWL_SPACES_SECONDARY_ENDPOINT"
        printf '%s\n' 'acl = private'
      fi
    } >"$RCLONE_CONF"
    if [[ -n "${BOWL_SPACES_SECONDARY_ENDPOINT:-}" && -n "${BOWL_SPACES_SECONDARY_BUCKET:-}" ]]; then
      rclone mkdir "bowl-spaces-dr:${BOWL_SPACES_SECONDARY_BUCKET}" --config "$RCLONE_CONF" 2>/dev/null || true
    fi
    echo "Wrote $RCLONE_CONF"
  else
    echo "Spaces keys still empty in $ENV_FILE — offsite sync will no-op until configured."
  fi
fi

chmod 755 "$APP/deploy/vps/sync-backups-offsite.sh" "$APP/deploy/vps/sync-backups-offsite-secondary.sh"
install -m 644 "$APP/deploy/vps/bowl-backup-offsite.service" /etc/systemd/system/bowl-backup-offsite.service
install -m 644 "$APP/deploy/vps/bowl-backup-offsite-secondary.service" /etc/systemd/system/bowl-backup-offsite-secondary.service
install -m 644 "$APP/deploy/vps/bowl-backup-offsite.timer" /etc/systemd/system/bowl-backup-offsite.timer
systemctl daemon-reload
systemctl enable --now bowl-backup-offsite.timer
systemctl list-timers bowl-backup-offsite.timer --no-pager
