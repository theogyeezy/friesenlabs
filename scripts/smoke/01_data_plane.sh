#!/usr/bin/env bash
# Data-plane smoke: the schema/roles SQL parse and the RLS contract holds (static; no DB needed).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PY="${ROOT}/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

echo "[smoke:data] parse db/schema.sql + db/roles.sql and assert FORCE'd RLS on every table"
"$PY" -m pytest tests/unit/test_sql_schema.py -q

echo "[smoke:data] isolation harness runs (pending real DB)"
"$PY" scripts/isolation_test.py >/dev/null

echo "[smoke:data] OK"
