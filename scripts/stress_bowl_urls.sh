#!/usr/bin/env bash
# Quick load smoke test (run on VPS or PC with curl).
set -euo pipefail
HOST="${1:-127.0.0.1:8000}"
BASE="${2:-http://$HOST}"
H="${BOWL_HOST_HEADER:-www.bowlhockey.com}"
SLUG="${3:-}"
PATHS=(
  "/bowl-fantasy/"
  "/bowl-historical/"
  "/bowl-cap/"
)
if [[ -n "$SLUG" ]]; then
  PATHS+=("/bowl-fantasy/team/$SLUG" "/bowl-cap/team/$SLUG")
fi
CONC="${CONC:-10}"
REQS="${REQS:-5}"

echo "Target: $BASE (Host: $H) conc=$CONC reqs/path=$REQS"
for p in "${PATHS[@]}"; do
  echo "=== $p ==="
  tmp=$(mktemp)
  for ((r=0; r<REQS; r++)); do
    for ((c=0; c<CONC; c++)); do
      curl -sS -o /dev/null -w "%{http_code} %{time_total}\n" \
        -H "Host: $H" "${BASE}${p}" >>"$tmp" 2>/dev/null &
    done
  done
  wait
  awk '{code=$1; t=$2; if(code!=200) bad++; sum+=t; if(NR==1||t<min)min=t; if(NR==1||t>max)max=t} END{
    if(NR==0){print "no samples"; exit}
    printf "n=%d bad=%d min=%.3fs avg=%.3fs max=%.3fs\n", NR, bad+0, min, sum/NR, max
  }' "$tmp"
  rm -f "$tmp"
done
