#!/usr/bin/env bash
# Roll-up smoke: run every per-feature smoke in scripts/smoke/ in order. Any failure fails the run.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
shopt -s nullglob
for s in scripts/smoke/*.sh; do
  echo "=== $s ==="
  if bash "$s"; then
    echo "--- PASS: $s"
  else
    echo "--- FAIL: $s"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "[smoke_all] FAILED"
  exit 1
fi
echo "[smoke_all] ALL PASS"
