#!/usr/bin/env bash
# lib/rules.sh — layered critical rules generator.
# Composition order (later layers override / extend earlier ones):
#   1. Base rules          — apply to every project
#   2. Type rules          — keyed off IMPL_TYPE (backend/frontend/etc.)
#   3. Project rules       — read from .ralph/rules.md in the current working dir
#
# Output is a single markdown bullet list passed to {{CRITICAL_RULES}}.

_rules_base() {
  cat <<'EOF'
- After completing each unit, re-read the spec and plan before picking the next one.
- Do not mark a unit [x] until every test in its Test Plan section passes unmodified.
- If stuck on a single blocker for more than 3 tool-call rounds, mark the unit [!] Blocked with a clear reason and move on.
- Never skip, xfail, or comment out failing tests — fix the root cause or mark the unit blocked.
- Schema changes must be strictly additive unless the spec explicitly requires otherwise.
EOF
}

_rules_for_type() {
  local t="$1"
  case "$t" in
    backend|backend+frontend)
      cat <<'EOF'
- All schema changes via Alembic migrations — no raw SQL. Verify the down-migration runs cleanly before marking a migration unit complete.
- Every protected endpoint must have an explicit auth check; missing auth fails the unit.
- Async paths must not contain sync I/O (no `requests`, no blocking SQL in async views).
- Pydantic models for all request/response shapes; no untyped dict passthrough.
EOF
      ;;
  esac
  case "$t" in
    frontend|backend+frontend)
      cat <<'EOF'
- All colors via CSS variables or Tailwind utilities — no raw hex in component code.
- Define TypeScript types before implementation; no `any` escapes in new code.
- Server state via React Query; client UI state via Zustand. No `useState` for server data.
- Components with lists > 50 items must use virtualization (`react-window` or equivalent).
EOF
      ;;
  esac
  case "$t" in
    api)
      cat <<'EOF'
- Update the OpenAPI schema in the same change; assert round-trip in tests.
- All endpoints must declare pagination limits; no unbounded list endpoints.
EOF
      ;;
    database)
      cat <<'EOF'
- Every migration unit must include a verified rollback procedure.
- Index changes require a query-plan check captured in the unit's Notes.
EOF
      ;;
    infrastructure)
      cat <<'EOF'
- No secrets in plaintext — use env files, CI secrets, or Docker secrets exclusively.
- Healthchecks must be substantive (real readiness probe, not `test: true`).
EOF
      ;;
  esac
}

_rules_project() {
  # Project-level overrides live in .ralph/rules.md as a markdown bullet list.
  # Each non-empty, non-comment line starting with "- " is appended verbatim.
  local f=".ralph/rules.md"
  [[ -f "$f" ]] || return 0
  grep -E '^\s*- ' "$f" 2>/dev/null || true
}

# build_layered_rules <impl_type>  →  stdout: deduplicated bullet list
build_layered_rules() {
  local t="${1:-feature}"
  {
    _rules_base
    _rules_for_type "$t"
    _rules_project
  } | awk '
    /^[[:space:]]*-/ {
      line=$0
      sub(/^[[:space:]]+/, "", line)
      if (!seen[line]++) print line
    }
  '
}
