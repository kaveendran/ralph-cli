#!/usr/bin/env bash
# Layer 2 — API integration tests against an isolated Docker stack (test-db +
# backend-test). Volumes are always wiped afterwards (`down -v`), so every run
# starts from a clean database. Quiet when it passes.
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

trap 'docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1' EXIT
run "test stack up" docker compose -f docker-compose.test.yml up -d --wait --quiet-pull test-db backend-test
[[ $failed -eq 0 ]] && run "api tests" bash -c "cd backend && python -m pytest tests/api/ -x -q -p no:cacheprovider $*"
[[ $failed -eq 0 && $empty -eq 1 ]] && exit 5
exit $failed
