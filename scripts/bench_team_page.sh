#!/usr/bin/env bash
set -euo pipefail
H="${BOWL_HOST_HEADER:-www.bowlhockey.com}"
U="${1:-http://127.0.0.1:8000/bowl-fantasy/team/bgk-t2}"
for i in 1 2 3 4 5; do
  curl -sS -o /dev/null -w "run $i: %{time_total}s http=%{http_code}\n" -H "Host: $H" "$U"
done
