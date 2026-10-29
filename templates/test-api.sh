#!/usr/bin/env bash
# Layer 2 — API integration tests: Docker-isolated backend + test DB.
# Spins up test-db and backend-test, runs tests/api/, tears down with -v.
# Run after Layer 1 unit tests pass for any backend unit.
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$ROOT"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  API integration tests (Layer 2 — Docker)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Always wipe volumes on exit so the next run starts with a clean DB.
teardown() {
  echo ""
  echo "Tearing down test stack (wiping volumes)..."
  docker compose -f docker-compose.test.yml down -v
}
trap teardown EXIT

echo ""
echo "Starting test-db and backend-test..."
docker compose -f docker-compose.test.yml up -d --wait test-db backend-test

echo ""
echo "── API tests ────────────────────────────"
cd backend && python -m pytest tests/api/ -v "$@"
