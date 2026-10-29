#!/usr/bin/env bash
# Layer 3 — E2E / UI tests: full Docker stack + Playwright.
#
# Usage:
#   ./scripts/test-e2e.sh          full automated run (up → test → down -v)
#   ./scripts/test-e2e.sh --up     start stack only (for interactive Playwright MCP session)
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  E2E tests (Layer 3 — full Docker stack)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [[ "${1:-}" == "--up" ]]; then
  echo ""
  echo "Starting full test stack (leaving running for Playwright MCP)..."
  docker compose -f docker-compose.test.yml up -d --wait
  echo ""
  echo "Stack is ready:"
  echo "  Frontend (test): http://localhost:5174"
  echo "  Backend  (test): http://localhost:8001"
  echo ""
  echo "Use Playwright MCP browser tools against http://localhost:5174"
  echo "Or run: cd e2e && npx playwright test"
  echo ""
  echo "Tear down when done:"
  echo "  docker compose -f docker-compose.test.yml down -v"
  exit 0
fi

# Full automated run
teardown() {
  echo ""
  echo "Tearing down test stack (wiping volumes)..."
  docker compose -f docker-compose.test.yml down -v
}
trap teardown EXIT

echo ""
echo "Starting full test stack..."
docker compose -f docker-compose.test.yml up -d --wait

echo ""
echo "── Playwright tests ─────────────────────"
cd e2e && npx playwright test "$@"
