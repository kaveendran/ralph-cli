#!/usr/bin/env bash
# Layer 1 — unit tests: no Docker, fast. The harness runs this after every unit.
# Quiet when it passes, the failing part when it doesn't, and it stops at the
# first failure (pytest -x, vitest --bail), so a red run costs little context.
set -uo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
failed=0 empty=0
# run <label> <command…>: prints ✓ when it passes, else the last 80 lines of its output.
# A suite that found no tests (pytest exit 5) counts as empty, not failed.
run() {
  local label="$1"; shift
  local out rc
  out="$("$@" 2>&1)"; rc=$?
  if [[ $rc -eq 0 ]]; then echo "✓ $label"
  elif [[ $rc -eq 5 ]]; then echo "✓ $label (no tests yet)"; empty=1
  else echo "✗ $label (exit $rc)"; echo "$out" | tail -80; failed=1
  fi
}

if [[ -d backend ]]; then
  run "backend tests" bash -c 'cd backend && python -m pytest -x -q -p no:cacheprovider'
fi
if [[ -d frontend ]]; then
  run "frontend tests" bash -c 'cd frontend && npx vitest run --bail 1'
  run "typescript" bash -c 'cd frontend && npx tsc --noEmit'
fi
[[ $failed -eq 0 && $empty -eq 1 && ! -d frontend ]] && exit 5
exit $failed
