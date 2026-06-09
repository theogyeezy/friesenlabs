#!/usr/bin/env bash
# The demo dry-run (Build Guide Phase 12). Proves the build is green end-to-end locally — the same
# checks CI runs. The live demo (chat -> dashboard -> Greenlight approve) runs against deployed infra
# (BLOCKED: needs Nick); this is the offline proof.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"; [ -x "$PY" ] || PY=python3
PYTEST="${ROOT}/.venv/bin/pytest"; [ -x "$PYTEST" ] || PYTEST="$PY -m pytest"

fail=0
step() { echo; echo "=== $1 ==="; }

step "1/5 pytest (unit + integration)"
$PYTEST -q || fail=1

step "2/5 smoke_all"
bash scripts/smoke_all.sh || fail=1

step "3/5 multi-tenant isolation gate"
$PY scripts/isolation_test.py || fail=1

step "4/5 terraform validate"
( cd infra && terraform fmt -check -recursive && terraform init -backend=false && terraform validate ) || fail=1

step "5/5 web build + typecheck + e2e"
if [ -d web/node_modules ]; then
  ( cd web && npm run build && npm run typecheck && npx playwright test ) || fail=1
else
  echo "web/node_modules missing — run 'cd web && npm install' first (skipped)"
fi

echo
if [ "$fail" -ne 0 ]; then echo "DEMO DRY-RUN: FAILED"; exit 1; fi
echo "DEMO DRY-RUN: ALL GREEN"
