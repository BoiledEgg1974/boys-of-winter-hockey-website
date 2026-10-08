#!/usr/bin/env bash
# Warm homepage JSON disk cache after gunicorn restart (run via bowl-cache-warm.service).
set -uo pipefail

HOST="${BOWL_WARM_HOST:-www.bowlhockey.com}"
UPSTREAM="${BOWL_WARM_UPSTREAM:-http://127.0.0.1:8000}"
WAIT_SECS="${BOWL_WARM_WAIT_SECS:-90}"
CURL_MAX="${BOWL_WARM_CURL_MAX:-180}"
LEAGUES=(bowl-fantasy bowl-historical bowl-cap)

log() { echo "[bowl-warm] $(date -Is) $*"; }

ready=0
for ((i = 1; i <= WAIT_SECS; i++)); do
  if curl -sf -o /dev/null -H "Host: ${HOST}" "${UPSTREAM}/" --max-time 3; then
    ready=1
    break
  fi
  sleep 1
done

if [[ "${ready}" -ne 1 ]]; then
  log "gunicorn not ready after ${WAIT_SECS}s — skipping warm"
  exit 0
fi

log "warming homepage summary (segment=rs) for: ${LEAGUES[*]}"
fail=0
for lg in "${LEAGUES[@]}"; do
  url="${UPSTREAM}/${lg}/api/homepage/summary?segment=rs"
  code="$(curl -sS -o /dev/null -w '%{http_code}' -H "Host: ${HOST}" --max-time "${CURL_MAX}" "${url}" || echo "000")"
  log "${lg} -> HTTP ${code}"
  if [[ "${code}" != "200" ]]; then
    fail=1
  fi
done

exit "${fail}"
