#!/usr/bin/env bash
# ralph critique — council-of-experts plan review before build mode.
# Spawns 4 parallel Claude subagents (architect, tester, risk analyst, domain
# expert), then synthesizes their findings into one prioritized report.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/tui.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
CR_NUMBER=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cr) CR_NUMBER="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Resolve CR ────────────────────────────────────────────────────────────────
if [[ -z "$CR_NUMBER" ]]; then
  LATEST=""
  for d in development/cr*/; do
    n="${d#development/cr}"
    n="${n%/}"
    if [[ "$n" =~ ^[0-9]+$ ]]; then
      for f in "${d}plan/"*"-implementation-plan.md"; do
        if [[ -f "$f" ]]; then
          if [[ -z "$LATEST" || "$n" -gt "$LATEST" ]]; then
            LATEST="$n"
          fi
        fi
      done
    fi
  done
  CR_NUMBER="${LATEST:-}"
fi

if [[ -z "$CR_NUMBER" ]]; then
  echo "Error: No CR with a plan found. Use --cr N to specify one."
  exit 1
fi

CR_DIR="development/cr${CR_NUMBER}"
CR_NAME="CR${CR_NUMBER}"
PLAN_FILE=""
for f in "${CR_DIR}/plan/"*"-implementation-plan.md"; do
  [[ -f "$f" ]] && PLAN_FILE="$f" && break
done

if [[ -z "$PLAN_FILE" ]]; then
  echo "Error: No plan file found in ${CR_DIR}/plan/"
  echo "Run 'ralph start --mode plan --cr ${CR_NUMBER}' first."
  exit 1
fi

# ── Detect implementation type for domain expert ──────────────────────────────
IMPL_TYPE="feature"
BUILD_PROMPT="${CR_DIR}/prompts/build.md"
if [[ -f "$BUILD_PROMPT" ]]; then
  if grep -qi 'backend+frontend\|backend.*frontend\|full.stack' "$BUILD_PROMPT" 2>/dev/null; then
    IMPL_TYPE="backend+frontend"
  elif grep -qi 'python.*fastapi\|fastapi\|sqlalchemy\|pytest' "$BUILD_PROMPT" 2>/dev/null; then
    IMPL_TYPE="backend"
  elif grep -qi 'react\|vite\|vitest\|typescript.*frontend' "$BUILD_PROMPT" 2>/dev/null; then
    IMPL_TYPE="frontend"
  fi
fi

tui_header "critique — $CR_NAME" "plan: $PLAN_FILE  |  type: $IMPL_TYPE"

if ! command -v claude &>/dev/null; then
  echo "Error: 'claude' CLI not found in PATH."
  exit 1
fi

PLAN_CONTENT=$(cat "$PLAN_FILE")

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

ARCHITECT_OUT="$TMP_DIR/architect.txt"
TESTER_OUT="$TMP_DIR/tester.txt"
RISK_OUT="$TMP_DIR/risk.txt"
DOMAIN_OUT="$TMP_DIR/domain.txt"
INFRA_OUT="$TMP_DIR/infra.txt"
SECURITY_OUT="$TMP_DIR/security.txt"
PERF_OUT="$TMP_DIR/performance.txt"
DOCS_OUT="$TMP_DIR/docs.txt"

# ── Build domain focus block by impl type ─────────────────────────────────────
build_domain_focus() {
  local t="$1"
  case "$t" in
    backend+frontend)
      printf 'Backend focus areas:\n1. API design — RESTful, consistent naming, correct HTTP methods and status codes.\n2. Data model — Schema changes safe? Missing indexes? N+1 risks? Migration reversibility?\n3. Async/concurrency — Race conditions, missing transaction boundaries, async patterns correct?\n4. Security — Auth checks on all protected endpoints? Input validation? SQL injection risks?\n5. Performance — Missing pagination, unindexed queries, blocking calls in async paths?\n\nFrontend focus areas:\n1. Component design — Properly decomposed? Missing shared components? Prop drilling?\n2. State management — Zustand for client state, React Query for server state? Over-fetching?\n3. TypeScript — Types defined upfront? No implicit any escapes?\n4. Accessibility — ARIA labels, keyboard navigation, screen reader support planned?\n5. Performance — Missing memoization? Large lists without virtualization?'
      ;;
    backend)
      printf '1. API design — RESTful, consistent naming, correct HTTP methods and status codes.\n2. Data model — Schema changes safe? Missing indexes? N+1 query risks? Migration reversibility?\n3. Async/concurrency — Race conditions, missing transaction boundaries, async patterns correct?\n4. Security — Auth checks on all protected endpoints? Input validation with Pydantic? SQL injection?\n5. Performance — Missing pagination, unindexed queries, synchronous blocking calls in async paths?\n6. Testing — pytest-asyncio used correctly? Test DB isolation? Fixtures properly scoped?'
      ;;
    frontend)
      printf '1. Component design — Properly decomposed? Missing shared components? Prop drilling risks?\n2. State management — Zustand for client state, React Query for server state? Over-fetching?\n3. TypeScript — Types defined before implementation? Shared types between components? No any usage?\n4. Accessibility — ARIA labels planned? Keyboard navigation? Screen reader support?\n5. Performance — Missing memoization, large lists without virtualization, unnecessary re-renders?\n6. Testing — Vitest + RTL tests planned? User interaction testing? Async state testing?'
      ;;
    *)
      printf '1. Core logic correctness — Are algorithms and data flows specified clearly enough to implement?\n2. Error handling — Are failure modes documented per unit?\n3. Configuration — Are environment variables, secrets, and config documented?\n4. Integration points — Are all external system contracts clearly defined?\n5. Observability — Logging and error reporting planned where needed?'
      ;;
  esac
}

DOMAIN_FOCUS="$(build_domain_focus "$IMPL_TYPE")"

# ── Read docker-compose.test.yml if present (for infra expert) ────────────────
COMPOSE_TEST_CONTENT=""
if [[ -f "docker-compose.test.yml" ]]; then
  COMPOSE_TEST_CONTENT=$(cat "docker-compose.test.yml")
fi

# ── Launch 8 experts in parallel ──────────────────────────────────────────────
tui_section "Launching council of experts (8 parallel reviews)"

printf 'You are a senior software architect reviewing an implementation plan before coding starts.\n\nIdentify structural and design problems. Cite unit IDs throughout.\n\nReview:\n1. Unit decomposition — right size? (target 1–3 days each) Too large, too small, poorly bounded?\n2. Dependency graph — correct? Circular deps? Missing deps? Units that could parallelize but are serialized?\n3. Scope clarity — does each unit have an unambiguous Goal? Flag where "what gets built" is unclear.\n4. Interface contracts — units handing off to each other have explicit contracts? Flag implicit assumptions.\n5. Missing units — implied by plan context but absent from the unit list?\n\nOutput:\n## Architect Review\n\n### Unit decomposition issues\n(list citing unit IDs, or "None identified")\n\n### Dependency issues\n(list or "None identified")\n\n### Scope clarity issues\n(list or "None identified")\n\n### Missing units\n(list or "None identified")\n\n### Top 3 architectural concerns\n1.\n2.\n3.\n\n---\nPLAN:\n%s\n' \
  "$PLAN_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$ARCHITECT_OUT" 2>/dev/null &
ARCHITECT_PID=$!

printf 'You are a senior test engineer reviewing an implementation plan before coding starts.\n\nFind test coverage problems before implementation begins. Cite unit IDs and AC numbers throughout.\n\nReview:\n1. Coverage gaps — which units have weak, vague, or missing test plans?\n2. Untestable ACs — which use vague language ("works correctly", "handles errors") with no concrete check?\n3. Missing edge cases — error paths, boundary conditions, concurrency, failure modes not mentioned?\n4. Test dependency issues — tests that implicitly depend on other units being complete first?\n5. Integration gaps — multi-unit scenarios not planned anywhere?\n\nOutput:\n## Test Engineer Review\n\n### Coverage gaps by unit\n(list or "None identified")\n\n### Untestable acceptance criteria\n(list citing AC numbers, or "None identified")\n\n### Missing edge cases\n(list or "None identified")\n\n### Integration gaps\n(list or "None identified")\n\n### Top 3 testing concerns\n1.\n2.\n3.\n\n---\nPLAN:\n%s\n' \
  "$PLAN_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$TESTER_OUT" 2>/dev/null &
TESTER_PID=$!

printf 'You are a senior risk analyst reviewing an implementation plan before coding starts.\n\nIdentify what is most likely to go wrong during implementation. Cite unit IDs throughout.\n\nReview:\n1. High-risk units — significant unknowns, external dependencies, complex interactions, major refactors.\n2. Underspecified requirements — multiple valid interpretations, missing examples, undefined external behavior.\n3. External dependency blockers — third-party APIs, services, or infrastructure that could block progress.\n4. Irreversible changes — schema migrations, data transforms, API breaking changes hard to roll back.\n5. Integration risk — units that must fit together exactly with underspecified contracts between them.\n\nOutput:\n## Risk Analyst Review\n\n### High-risk units (most risky first)\n(list with one-line risk reason each, or "None identified")\n\n### Underspecified requirements\n(list or "None identified")\n\n### External dependency blockers\n(list or "None identified")\n\n### Irreversible changes\n(list or "None identified")\n\n### Top 3 risk concerns\n1.\n2.\n3.\n\n---\nPLAN:\n%s\n' \
  "$PLAN_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$RISK_OUT" 2>/dev/null &
RISK_PID=$!

printf 'You are a senior engineer reviewing an implementation plan before coding starts.\n\nYour focus: implementation correctness for this specific tech stack. Cite unit IDs throughout.\n\nReview these domain-specific areas:\n%s\n\nOutput:\n## Domain Expert Review\n\n### Issues found (cite unit IDs)\n(numbered list, or "None identified")\n\n### Top 3 domain concerns\n1.\n2.\n3.\n\n---\nPLAN:\n%s\n' \
  "$DOMAIN_FOCUS" "$PLAN_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$DOMAIN_OUT" 2>/dev/null &
DOMAIN_PID=$!

printf 'You are a senior infrastructure engineer reviewing Docker test infrastructure before a build loop starts.\n\nIf no docker-compose.test.yml content is provided below, output exactly:\n## Infrastructure Reviewer\n\nNo docker-compose.test.yml found — no infrastructure review possible.\n\nOtherwise, review the file for these issues:\n1. Missing or weak healthchecks — services without a `healthcheck:` block, or using `test: ["CMD", "true"]` (trivially passes).\n2. Broken dependency chain — services that should wait for an upstream to be healthy but use `depends_on:` without `condition: service_healthy`.\n3. Hardcoded secrets — passwords, API keys, or tokens in plaintext (not from env files, CI secrets, or Docker secrets).\n4. Teardown without -v — any teardown that uses `down` without `-v`, leaving volumes dirty between runs.\n5. Port conflicts with dev — test services mapped to the same host ports as the standard dev docker-compose.yml (e.g. test-db on 5432 if dev postgres also uses 5432).\n6. Shared networks with dev — test services on the same named network as dev services, allowing cross-contamination.\n\nOutput:\n## Infrastructure Reviewer\n\n### Missing or weak healthchecks\n(list citing service names, or "None identified")\n\n### Broken dependency chain\n(list or "None identified")\n\n### Hardcoded secrets\n(list citing service/line, or "None identified")\n\n### Teardown issues\n(list or "None identified")\n\n### Port or network conflicts with dev\n(list or "None identified")\n\n### Top 3 infrastructure concerns\n1.\n2.\n3.\n\n---\ndocker-compose.test.yml:\n%s\n' \
  "$COMPOSE_TEST_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$INFRA_OUT" 2>/dev/null &
INFRA_PID=$!

printf 'You are a senior application security engineer reviewing an implementation plan before coding starts.\n\nIdentify security weaknesses BEFORE they are implemented. Cite unit IDs throughout. Map findings to OWASP Top 10 categories where relevant (A01 Broken Access Control, A02 Cryptographic Failures, A03 Injection, A04 Insecure Design, A05 Security Misconfig, A07 Auth Failures, A08 Software/Data Integrity, A09 Logging Failures, A10 SSRF).\n\nReview:\n1. AuthN/AuthZ gaps — endpoints or operations the plan introduces without explicit auth/permission checks.\n2. Input validation — units that accept untrusted input without an explicit validation strategy.\n3. Injection vectors — SQL, command, template, LDAP, NoSQL, header injection risks in any new code path.\n4. Sensitive data exposure — PII, tokens, secrets, audit fields handled without explicit protection plan.\n5. Authorization model — multi-tenant boundaries, IDOR risks, missing object-level permission checks.\n6. Cryptography — weak primitives, hardcoded keys, missing TLS, weak password hashing.\n7. Audit logging — security-relevant actions (auth, role changes, data export) without logging plan.\n8. Dependency risk — new third-party libraries introduced without a clear update/scan strategy.\n\nOutput:\n## Security Reviewer\n\n### AuthN / AuthZ gaps\n(list citing unit IDs + OWASP code, or "None identified")\n\n### Input validation gaps\n(list or "None identified")\n\n### Injection risks\n(list or "None identified")\n\n### Sensitive data handling\n(list or "None identified")\n\n### Audit logging gaps\n(list or "None identified")\n\n### Top 3 security concerns\n1.\n2.\n3.\n\n---\nPLAN:\n%s\n' \
  "$PLAN_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$SECURITY_OUT" 2>/dev/null &
SECURITY_PID=$!

printf 'You are a senior performance engineer reviewing an implementation plan before coding starts.\n\nIdentify performance, scalability, and cost issues BEFORE they are implemented. Cite unit IDs throughout.\n\nReview:\n1. N+1 query risks — units that iterate over collections issuing per-item DB calls.\n2. Missing pagination — list endpoints, query units, or UI lists with no declared upper bound.\n3. Missing indexes — schema changes that introduce filterable/sortable columns without an index plan.\n4. Blocking calls in async paths — sync I/O inside async handlers, blocking subprocess calls in event loops.\n5. Unbounded resources — in-memory caches without TTL/LRU, queues without backpressure, retries without jitter or cap.\n6. Hot path inefficiencies — repeated work that should be memoized, expensive operations on user-blocking paths.\n7. External API cost — units that call paid APIs without rate limiting, caching, or batching.\n8. Frontend perf — large lists without virtualization, missing memoization on hot components, large bundle additions.\n\nOutput:\n## Performance Reviewer\n\n### Query / data-access issues\n(list citing unit IDs, or "None identified")\n\n### Missing pagination / unbounded results\n(list or "None identified")\n\n### Missing indexes\n(list or "None identified")\n\n### Blocking / sync-in-async issues\n(list or "None identified")\n\n### Cost or rate-limit concerns\n(list or "None identified")\n\n### Top 3 performance concerns\n1.\n2.\n3.\n\n---\nPLAN:\n%s\n' \
  "$PLAN_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$PERF_OUT" 2>/dev/null &
PERF_PID=$!

printf 'You are a senior technical writer reviewing an implementation plan before coding starts.\n\nIdentify documentation gaps that will surface as confusion AFTER the code ships. Cite unit IDs.\n\nReview:\n1. User-facing docs missing — units that change observable behavior without a documentation update plan.\n2. API docs — endpoint changes without OpenAPI / reference doc updates.\n3. Migration / upgrade notes — schema, config, or breaking changes without a release-note plan.\n4. AGENTS.md drift — new run/build/test commands not flagged for AGENTS.md update.\n5. CLAUDE.md / project-standards drift — new conventions (folder layout, naming) not captured.\n6. Onboarding impact — units that touch first-run / setup paths without README updates.\n7. Inline doc gaps — public APIs, complex algorithms, or non-obvious invariants without docstring/comment plan.\n\nOutput:\n## Documentation Reviewer\n\n### Missing user-facing docs\n(list citing unit IDs, or "None identified")\n\n### Missing API / reference docs\n(list or "None identified")\n\n### Missing migration / release notes\n(list or "None identified")\n\n### AGENTS.md / CLAUDE.md drift risks\n(list or "None identified")\n\n### Top 3 documentation concerns\n1.\n2.\n3.\n\n---\nPLAN:\n%s\n' \
  "$PLAN_CONTENT" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    > "$DOCS_OUT" 2>/dev/null &
DOCS_PID=$!

# ── Wait for all 8 experts ────────────────────────────────────────────────────
tui_wait_pids "Council of experts reviewing plan..." \
  "$ARCHITECT_PID" "$TESTER_PID" "$RISK_PID" "$DOMAIN_PID" "$INFRA_PID" \
  "$SECURITY_PID" "$PERF_PID" "$DOCS_PID"

wait "$ARCHITECT_PID" "$TESTER_PID" "$RISK_PID" "$DOMAIN_PID" "$INFRA_PID" \
     "$SECURITY_PID" "$PERF_PID" "$DOCS_PID" 2>/dev/null || true

tui_section "All experts complete — synthesizing findings"

# ── Synthesizer ───────────────────────────────────────────────────────────────
ARCHITECT_REVIEW=$(cat "$ARCHITECT_OUT")
TESTER_REVIEW=$(cat "$TESTER_OUT")
RISK_REVIEW=$(cat "$RISK_OUT")
DOMAIN_REVIEW=$(cat "$DOMAIN_OUT")
INFRA_REVIEW=$(cat "$INFRA_OUT")
SECURITY_REVIEW=$(cat "$SECURITY_OUT")
PERF_REVIEW=$(cat "$PERF_OUT")
DOCS_REVIEW=$(cat "$DOCS_OUT")

# Persist the synthesized report so refine-plan / retro can read it later.
REPORT_DIR="${CR_DIR}/critique"
mkdir -p "$REPORT_DIR"
REPORT_FILE="${REPORT_DIR}/$(date +%Y%m%d-%H%M%S)-report.md"

printf 'You are a senior engineering lead synthesizing eight expert reviews of an implementation plan.\n\nProduce one prioritized action report the developer can act on before starting the build loop.\n\nRules:\n- De-duplicate: if multiple experts flag the same issue, list it once with the clearest framing.\n- Prioritize: (1) issues that cause build failures or wrong output, (2) issues requiring rework after units are marked complete, (3) quality improvements.\n- Treat infrastructure issues (broken healthcheck chain, missing `down -v`) with CRITICAL severity — they silently corrupt test results.\n- Treat security issues with CRITICAL severity when they involve missing auth, injection, or sensitive-data exposure.\n- Be specific: every item must reference a unit ID, AC number, service name, or plan section.\n- Be actionable: every item must end with the exact edit to make to the plan or compose file.\n- For each item, append a stable tag in the form `[origin:<expert>]` so the refine step can map fixes back to source.\n\nOutput:\n\n## Ralph Critique Report — %s\n\n### CRITICAL — Fix before starting build\n(Will cause failures or require rework. Numbered list, or "None.")\n\n### IMPORTANT — Fix before starting build\n(Risky but survivable. Numbered list, or "None.")\n\n### SUGGESTED — Consider before starting\n(Quality improvements. Numbered list, or "None.")\n\n### Plan health\n(2–3 sentences: overall quality, key strengths, confidence level for starting build.)\n\n---\nARCHITECT REVIEW:\n%s\n\n---\nTEST ENGINEER REVIEW:\n%s\n\n---\nRISK ANALYST REVIEW:\n%s\n\n---\nDOMAIN EXPERT REVIEW:\n%s\n\n---\nINFRASTRUCTURE REVIEW:\n%s\n\n---\nSECURITY REVIEW:\n%s\n\n---\nPERFORMANCE REVIEW:\n%s\n\n---\nDOCUMENTATION REVIEW:\n%s\n' \
  "$CR_NAME" \
  "$ARCHITECT_REVIEW" \
  "$TESTER_REVIEW" \
  "$RISK_REVIEW" \
  "$DOMAIN_REVIEW" \
  "$INFRA_REVIEW" \
  "$SECURITY_REVIEW" \
  "$PERF_REVIEW" \
  "$DOCS_REVIEW" \
  | claude -p \
    --dangerously-skip-permissions \
    --output-format text \
    --model sonnet \
    | tee "$REPORT_FILE"

echo ""
tui_log_ok "Report saved to: $REPORT_FILE"

echo ""
if tui_available; then
  gum style --foreground 240 "─────────────────────────────────────────"
else
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi
echo ""
echo "Edit the plan to address any CRITICAL or IMPORTANT issues:"
echo "  ${PLAN_FILE}"
echo ""
echo "Then start the build loop:"
echo "  ralph start --mode build --cr ${CR_NUMBER}"
echo ""
