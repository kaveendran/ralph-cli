#!/usr/bin/env bash
# Layer 3 — end-to-end tests: the full Docker stack plus Playwright.
#
#   bash scripts/test-e2e.sh        up → test → down -v (quiet when it passes)
#   bash scripts/test-e2e.sh --up   start the stack and leave it running, e.g. to
#                                   drive it with the Playwright MCP server
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

if [[ "${1:-}" == "--up" ]]; then
  docker compose -f docker-compose.test.yml up -d --wait --quiet-pull || exit 1
  echo "Test stack is up: frontend http://localhost:5174, backend http://localhost:8001"
  echo "Tear it down with: docker compose -f docker-compose.test.yml down -v"
  exit 0
fi
trap 'docker compose -f docker-compose.test.yml down -v >/dev/null 2>&1' EXIT
run "test stack up" docker compose -f docker-compose.test.yml up -d --wait --quiet-pull
[[ $failed -eq 0 ]] && run "e2e tests" bash -c "cd e2e && npx playwright test --max-failures=1 --reporter=line $*"
exit $failed
