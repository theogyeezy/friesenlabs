#!/usr/bin/env bash
# Semantic-layer smoke: cube models + config are valid JS and the tenant security context holds.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "[smoke:semantic] syntax-check cube config + models"
for f in semantic/cube.js semantic/security.js semantic/model/cubes/*.js; do
  node --check "$f"
done

echo "[smoke:semantic] tenant security context tests (force tenant filter / throw without tenant)"
# Unquoted so the SHELL expands the glob (Node <21 doesn't expand globs in --test; CI runs Node 20).
node --test semantic/test/*.test.js >/dev/null

echo "[smoke:semantic] OK"
