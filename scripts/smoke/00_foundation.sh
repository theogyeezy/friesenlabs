#!/usr/bin/env bash
# Foundation smoke: the repo's scaffolding is coherent and the basics build/import.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "[smoke:foundation] python imports shared.config"
PYTHONPATH="$ROOT" python3 -c "from shared import config; assert config.load().project=='uplift'"

echo "[smoke:foundation] expected top-level dirs exist"
for d in infra api agents agents/roster agents/tools worker ingest semantic ml web shared tests scripts; do
  test -d "$d" || { echo "MISSING dir: $d"; exit 1; }
done

echo "[smoke:foundation] OK"
