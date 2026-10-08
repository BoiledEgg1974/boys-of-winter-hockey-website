#!/usr/bin/env bash
set -euo pipefail
PA_SSH=(ssh -i /root/.ssh/id_ed25519_pa -o BatchMode=yes -o StrictHostKeyChecking=accept-new)
PA=BoiledEgg1974@ssh.pythonanywhere.com
PA_APP=/home/BoiledEgg1974/boys-of-winter-hockey-website
PA_PS=/home/BoiledEgg1974/bowl-perfect-squad
LOG=/var/log/bowl-rsync.log
exec >>"$LOG" 2>&1
echo "=== bowl rsync start $(date -Is) ==="
rsync -avz -e "${PA_SSH[*]}" \
  --exclude .venv --exclude __pycache__ --exclude .git \
  "${PA}:${PA_APP}/" /srv/bowl/app/
echo "=== perfect-squad rsync start $(date -Is) ==="
rsync -avz -e "${PA_SSH[*]}" \
  --exclude .venv --exclude __pycache__ --exclude .git \
  "${PA}:${PA_PS}/" /srv/bowl/perfect-squad/
chown -R bowl:bowl /srv/bowl
touch /var/log/bowl-rsync.done
echo "=== done $(date -Is) ==="
