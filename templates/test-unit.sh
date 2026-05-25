#!/usr/bin/env bash
# Layer 1 — Unit tests: no Docker, fast. Run after every unit.
# Auto-detects backend/ and frontend/ so one script works for all impl types.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Unit tests (Layer 1 — no Docker)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

FAILED=0

if [[ -d backend ]]; then
  echo ""
  echo "── Backend ──────────────────────────────"
  (cd backend && python -m pytest -p no:cacheprovider --no-cov -q) || FAILED=1
fi

if [[ -d frontend ]]; then
  echo ""
  echo "── Frontend ─────────────────────────────"
  (cd frontend && npx vitest run) || FAILED=1
  echo ""
  echo "── TypeScript ───────────────────────────"
  (cd frontend && npx tsc --noEmit) || FAILED=1
fi

echo ""
if [[ $FAILED -eq 0 ]]; then
  echo "Layer 1 PASSED"
else
  echo "Layer 1 FAILED — fix all failures before proceeding"
fi

exit $FAILED
