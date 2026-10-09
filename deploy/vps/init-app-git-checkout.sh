#!/usr/bin/env bash
# Turn /srv/bowl/app into a git clone (keeps .env, instance/*.db, .venv as local-only).
# Run on the droplet as root: bash /srv/bowl/app/deploy/vps/init-app-git-checkout.sh
set -euo pipefail

APP="${BOWL_APP_ROOT:-/srv/bowl/app}"
ORIGIN="${BOWL_GIT_ORIGIN:-https://github.com/BoiledEgg1974/boys-of-winter-hockey-website.git}"
BRANCH="${BOWL_GIT_BRANCH:-master}"

if [[ ! -d "$APP" ]]; then
  echo "Missing $APP" >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git
fi

if [[ -d "$APP/.git" ]]; then
  echo "Already a git repo: $APP"
  exit 0
fi

echo "Initializing git in $APP (user bowl)..."
sudo -u bowl git config --global --add safe.directory "$APP"
git config --global --add safe.directory "$APP" 2>/dev/null || true

if [[ ! -d "$APP/.git" ]]; then
  sudo -u bowl git -C "$APP" init -b "$BRANCH"
fi
if ! sudo -u bowl git -C "$APP" remote get-url origin >/dev/null 2>&1; then
  sudo -u bowl git -C "$APP" remote add origin "$ORIGIN"
fi
sudo -u bowl git -C "$APP" fetch origin "$BRANCH"
sudo -u bowl git -C "$APP" reset --hard "origin/$BRANCH"
sudo -u bowl git -C "$APP" branch -u "origin/$BRANCH" "$BRANCH" 2>/dev/null || true

echo "Done. $(sudo -u bowl git -C "$APP" rev-parse --short HEAD) on $BRANCH"
echo "Local-only paths (.env, instance/*.db, .venv) stay on disk; use git pull/fetch+reset for code."
