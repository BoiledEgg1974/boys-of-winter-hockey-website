#!/usr/bin/env bash
# Run on the droplet as root after Namecheap A records point @ and www here.
set -euo pipefail

DROPLET_IP="${DROPLET_IP:-$(curl -fsS https://api.digitalocean.com/metadata/v1/interfaces/public/0/ipv4/address 2>/dev/null || hostname -I | awk '{print $1}')}"
ENV_FILE=/srv/bowl/app/.env
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"

if [[ -z "$CERTBOT_EMAIL" && -f "$ENV_FILE" ]]; then
  CERTBOT_EMAIL="$(grep -m1 '^MAIL_FROM=' "$ENV_FILE" | cut -d= -f2- | tr -d '\"' || true)"
fi
if [[ -z "$CERTBOT_EMAIL" ]]; then
  echo "Set CERTBOT_EMAIL=you@domain.com" >&2
  exit 1
fi

resolve_www() {
  dig +short www.bowlhockey.com A 2>/dev/null | head -1
}

www_ip="$(resolve_www || true)"
if [[ "$www_ip" != "$DROPLET_IP" ]]; then
  echo "DNS not ready: www.bowlhockey.com -> ${www_ip:-?} (want $DROPLET_IP)" >&2
  echo "Update Namecheap A records, wait a few minutes, re-run." >&2
  exit 2
fi

echo "DNS OK ($www_ip). Requesting certificate..."
certbot --nginx -d www.bowlhockey.com -d bowlhockey.com \
  --non-interactive --agree-tos -m "$CERTBOT_EMAIL" --redirect

systemctl enable --now bowl-discord-bot
systemctl restart bowl-web bowl-discord-bot

if [[ -f /root/.ssh/id_ed25519_pa ]]; then
  rm -f /root/.ssh/id_ed25519_pa
  echo "Removed /root/.ssh/id_ed25519_pa"
fi

echo "Cutover stack up. Disable PythonAnywhere always-on Discord task in the PA dashboard."
