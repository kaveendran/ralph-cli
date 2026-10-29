#!/usr/bin/env bash
# test/e2e.sh — End-to-end test suite for ralph-cli.
# Run from anywhere: bash /path/to/ralph-cli/test/e2e.sh
# Requires: ralph-cli installed, bash 4+
# Does NOT require Claude or gum (Claude tests are skipped; gum is bypassed via RALPH_NO_TUI).
set -uo pipefail

RALPH_CLI="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RALPH="$RALPH_CLI/ralph.sh"
TEST_DIR="/tmp/ralph-e2e-$$"
export RALPH_NO_TUI=1   # force plain fallback — gum reads from TTY, not stdin

PASS=0; FAIL=0; SKIP=0

G='\033[32m'; R='\033[31m'; Y='\033[33m'; B='\033[1m'; X='\033[0m'


pass()    { printf "${G}  ✓${X}  %s\n" "$1";         PASS=$((PASS+1)); }
fail()    { printf "${R}  ✗${X}  %s\n" "$1";         FAIL=$((FAIL+1)); }
skip()    { printf "${Y}  -${X}  %s (skipped)\n" "$1"; SKIP=$((SKIP+1)); }
section() { printf "\n${B}── %s ──${X}\n" "$1"; }


check_file() {
  local f="$1" label="${2:-$1}"
  [[ -f "$f" ]] && pass "file exists: $label" || fail "file missing: $label"
}


check_no_placeholders() {
  local f="$1"
  if grep -qE '\{\{[A-Z_]+\}\}' "$f" 2>/dev/null; then
    fail "unresolved placeholders in $(basename "$f")"
    grep -E '\{\{[A-Z_]+\}\}' "$f" | head -3 | sed 's/^/       /'
  else
    pass "no unresolved placeholders: $(basename "$f")"
  fi
}

check_contains() {
  local desc="$1" pattern="$2" text="$3"
  if echo "$text" | grep -qE -e "$pattern"; then
    pass "$desc"
  else
    fail "$desc — expected /$pattern/ in: $(echo "$text" | head -3)"
  fi
}

check_not_contains() {
  local desc="$1" pattern="$2" text="$3"
  if echo "$text" | grep -qE -e "$pattern"; then
    fail "$desc — did NOT expect /$pattern/"
  else
    pass "$desc"
  fi
}

mkdir -p "$TEST_DIR"
cd "$TEST_DIR"

printf "\n${B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}\n"
printf "${B}  Ralph CLI — End-to-End Test Suite${X}\n"
printf "  Dir: %s\n" "$TEST_DIR"
printf "${B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}\n\n"

# ──────────────────────────────────────────────────────────────────────────────
section "1. ralph help"
# ──────────────────────────────────────────────────────────────────────────────
OUT=$(bash "$RALPH" help 2>&1 || true)
check_contains "help lists init"     "init"     "$OUT"
check_contains "help lists add-cr"   "add-cr"   "$OUT"
check_contains "help lists start"    "start"    "$OUT"
check_contains "help lists status"   "status"   "$OUT"
check_contains "help lists critique" "critique" "$OUT"

# ──────────────────────────────────────────────────────────────────────────────
section "2. ralph init --manual (feature type, no docker)"
# ──────────────────────────────────────────────────────────────────────────────
# Pipe stdin: project name, description, impl type (4=feature), skip docker prompt
OUT=$(printf 'E2E Test Project\nA test project for e2e validation\n4\n' | \
  bash "$RALPH" init --manual --project-dir "$TEST_DIR" 2>&1 || true)

check_file "AGENTS.md"
check_file "CLAUDE.md"
check_file "loop_plan.sh"
check_file "loop_cr.sh"
check_file "development/initial/spec/spec.md"
check_file "development/initial/prompts/build.md"
check_file "development/initial/prompts/spec-planning.md"
check_file "development/initial/progress/implementation-progress.md"
check_file "development/templates/implementation-planning.md"
check_no_placeholders "development/initial/prompts/build.md"
check_no_placeholders "development/initial/prompts/spec-planning.md"
check_contains "EXISTING_CODE=false in init spec-planning" "false" \
  "$(grep 'EXISTING_CODE' development/initial/prompts/spec-planning.md || echo '')"

# ──────────────────────────────────────────────────────────────────────────────
section "3. ralph init --manual (backend+frontend, with docker infra)"
# ──────────────────────────────────────────────────────────────────────────────
DOCKER_DIR="/tmp/ralph-e2e-docker-$$"
mkdir -p "$DOCKER_DIR"
OUT=$(printf 'Docker Test Project\nTest with docker\n1\nY\n' | \
  bash "$RALPH" init --manual --project-dir "$DOCKER_DIR" 2>&1 || true)

check_file "$DOCKER_DIR/docker-compose.test.yml"
check_file "$DOCKER_DIR/scripts/test-unit.sh"
check_file "$DOCKER_DIR/scripts/test-api.sh"
check_file "$DOCKER_DIR/scripts/test-e2e.sh"
check_file "$DOCKER_DIR/e2e/playwright.config.ts"
check_file "$DOCKER_DIR/.claude/settings.json"
check_contains "test-unit.sh is executable" "x" \
  "$(ls -la "$DOCKER_DIR/scripts/test-unit.sh" | cut -c1-10)"
rm -rf "$DOCKER_DIR"

# ──────────────────────────────────────────────────────────────────────────────
section "4. ralph status — stage 1 (stub spec)"
# ──────────────────────────────────────────────────────────────────────────────
OUT=$(bash "$RALPH" status 2>&1 || true)
check_contains "stage 1-write-spec detected" "1-write-spec|WRITE SPEC" "$OUT"
check_contains "status shows spec path"       "spec.md"                  "$OUT"
check_contains "status shows next command"    "ralph start.*plan|start"  "$OUT"

# ──────────────────────────────────────────────────────────────────────────────
section "5. ralph status — stage 2 (real spec written)"
# ──────────────────────────────────────────────────────────────────────────────
cat > development/initial/spec/spec.md << 'SPEC'
# E2E Test Project

## 1. Project Overview
This is an end-to-end test project for validating the Ralph Loop CLI.
The system provides a simple task management API with full CRUD support.
Users interact through a REST API to manage their tasks efficiently.
The backend exposes a RESTful HTTP interface with JSON request and response bodies.

## 3. Technology Stack
- Python 3.11, FastAPI, SQLAlchemy, Pydantic, pytest, pytest-asyncio
- PostgreSQL 15 for relational data persistence and audit logging
- Alembic for schema migrations, pytest-asyncio for async test coverage

## 4. Functional Requirements
FR-001: Users can create tasks with a title and optional description.
FR-002: Users can list all tasks with cursor-based pagination.
FR-003: Users can update task status (pending, in-progress, done).
FR-004: Users can delete tasks by ID with soft-delete semantics.
FR-005: All state changes emit immutable audit log entries for traceability.

## 9. Acceptance Criteria
AC-001: All CRUD endpoints return correct HTTP status codes.
AC-002: Task creation validates required fields and returns 422 on failure.
AC-003: Pagination returns correct page sizes and next-cursor tokens.
AC-004: Soft-deleted tasks are excluded from list results by default.
AC-005: Audit log entries are created atomically with every state change.
SPEC

OUT=$(bash "$RALPH" status 2>&1 || true)
check_contains "stage 2-plan detected after spec written" "2-plan|PLAN" "$OUT"
check_not_contains "stage 1 no longer shown"              "1-write-spec"  "$OUT"

# ──────────────────────────────────────────────────────────────────────────────
section "6. ralph add-cr — all flags, no prompts"
# ──────────────────────────────────────────────────────────────────────────────
OUT=$(printf 'n\n' | bash "$RALPH" add-cr \
  --number 1 \
  --title "Task CRUD API" \
  --type backend \
  --notes "Focus on REST endpoints and pytest coverage" \
  2>&1 || true)

check_file "development/cr1/spec/spec.md"
check_file "development/cr1/prompts/build.md"
check_file "development/cr1/prompts/spec-planning.md"
check_file "development/cr1/progress/implementation-progress.md"
check_file "development/cr1/plan/cr1-implementation-plan.md"
check_no_placeholders "development/cr1/prompts/build.md"
check_no_placeholders "development/cr1/prompts/spec-planning.md"
check_contains "EXISTING_CODE=true for add-cr" "true" \
  "$(grep 'EXISTING_CODE' development/cr1/prompts/spec-planning.md || echo '')"
check_contains "special notes injected in build.md" "pytest coverage" \
  "$(grep -i 'pytest coverage' development/cr1/prompts/build.md || echo '')"
check_contains "git tag set to v0.1.0" "v0.1.0" \
  "$(grep 'GIT_TAG\|v0\.1\.0' development/cr1/prompts/build.md || echo '')"

# ──────────────────────────────────────────────────────────────────────────────
section "7. ralph add-cr — with --prior-cr and --spec-file"
# ──────────────────────────────────────────────────────────────────────────────
OUT=$(printf 'n\n' | bash "$RALPH" add-cr \
  --number 2 \
  --title "Task Frontend" \
  --type frontend \
  --prior-cr 1 \
  --spec-file development/initial/spec/spec.md \
  2>&1 || true)

check_file "development/cr2/spec/spec.md"
check_contains "prior-cr baseline link in build.md" "cr1" \
  "$(grep -i 'cr1\|prior' development/cr2/prompts/build.md || echo '')"
check_contains "spec-file copied correctly" "Acceptance Criteria" \
  "$(grep 'Acceptance Criteria' development/cr2/spec/spec.md || echo '')"

# ──────────────────────────────────────────────────────────────────────────────
section "8. ralph status --cr 1"
# ──────────────────────────────────────────────────────────────────────────────
OUT=$(bash "$RALPH" status --cr 1 2>&1 || true)
check_contains "status --cr 1 identifies CR1"         "CR1"            "$OUT"
check_contains "status --cr 1 shows stage 1 (no spec)" "1-write-spec|WRITE SPEC" "$OUT"

# Write spec for CR1 and recheck
cp development/initial/spec/spec.md development/cr1/spec/spec.md
OUT=$(bash "$RALPH" status --cr 1 2>&1 || true)
check_contains "status --cr 1 advances to stage 2"  "2-plan|PLAN"  "$OUT"

# ──────────────────────────────────────────────────────────────────────────────
section "9. ralph status — auto-detect latest CR"
# ──────────────────────────────────────────────────────────────────────────────
# Create a progress entry so status can detect the CR
printf -- '- [ ] U001 Setup project structure\n' > development/cr1/progress/implementation-progress.md
OUT=$(bash "$RALPH" status 2>&1 || true)
check_contains "status auto-detects latest CR (cr2 has no progress, cr1 does)" \
  "CR1|CR2|initial" "$OUT"

# ──────────────────────────────────────────────────────────────────────────────
section "10. lib/context.sh — generate_context"
# ──────────────────────────────────────────────────────────────────────────────
source "$RALPH_CLI/lib/context.sh"

# ── populate_progress_from_plan ───────────────────────────────────────────────
STUB_PROGRESS="$TEST_DIR/stub-progress.md"
FAKE_PLAN="$TEST_DIR/fake-plan.md"

cat > "$FAKE_PLAN" << 'EOF'
## Unit: Setup project structure (ID: U001)
## Unit: Create database models (ID: U002)
## Unit: Implement CRUD endpoints (ID: U003)
EOF

# stub progress file (no unit markers)
printf '# CR Test\n\nno units yet\n' > "$STUB_PROGRESS"
populate_progress_from_plan "$FAKE_PLAN" "$STUB_PROGRESS"
check_contains "populate: U001 written to progress" "U001" "$(cat "$STUB_PROGRESS")"
check_contains "populate: U002 written to progress" "U002" "$(cat "$STUB_PROGRESS")"
check_contains "populate: U003 written to progress" "U003" "$(cat "$STUB_PROGRESS")"
check_contains "populate: markers are not-started"  "^\- \[ \]" "$(cat "$STUB_PROGRESS")"

# idempotent — won't overwrite real progress (use a fresh file to avoid state pollution)
IDEM_PROGRESS="$TEST_DIR/idem-progress.md"
printf -- '- [x] U001 Done\n- [ ] U002 Not started\n' > "$IDEM_PROGRESS"
populate_progress_from_plan "$FAKE_PLAN" "$IDEM_PROGRESS"
check_contains "populate: idempotent when markers exist" "\[x\] U001" "$(cat "$IDEM_PROGRESS")"

# missing plan — no crash, no change
NOOP_PROGRESS="$TEST_DIR/noop-progress.md"
printf -- '- [x] U001 Done\n- [ ] U002 Not started\n' > "$NOOP_PROGRESS"
populate_progress_from_plan "/tmp/no-such-plan-$$" "$NOOP_PROGRESS"
check_contains "populate: no-op for missing plan" "\[x\] U001" "$(cat "$NOOP_PROGRESS")"

# all_units_done: stub progress (no markers) → false
printf '# stub\n\nno units yet\n' > "$STUB_PROGRESS"
all_units_done "$STUB_PROGRESS" \
  && fail "all_units_done: stub with no markers should return false" \
  || pass "all_units_done: correctly false for stub (no unit markers)"

PROG="$TEST_DIR/test-progress.md"
cat > "$PROG" << 'EOF'
- [x] U001 Set up FastAPI project structure
- [x] U002 Create database models
- [~] U003 Implement CRUD endpoints
- [ ] U004 Add authentication middleware
- [ ] U005 Add pagination support
- [!] U006 Add rate limiting (blocked: needs Redis, not in spec)
EOF

CTX=$(generate_context "$PROG")
check_contains "context: progress percentage"    "2/6|33%"       "$CTX"
check_contains "context: in-progress unit shown" "U003"           "$CTX"
check_contains "context: next unit shown"        "U004"           "$CTX"
check_contains "context: blocked unit listed"    "U006"           "$CTX"
check_contains "context: blocked prompts re-eval" "re-read|resolved" "$CTX"
check_contains "context: last commit line present" "Last commit"  "$CTX"

# all_units_done — mixed → false
all_units_done "$PROG" \
  && fail "all_units_done: mixed file should return false" \
  || pass "all_units_done: correctly false for mixed progress"

# all_units_done — all complete → true
printf -- '- [x] U001 Done\n- [x] U002 Done\n- [x] U003 Done\n' > "$PROG"
all_units_done "$PROG" \
  && pass "all_units_done: correctly true when all [x]" \
  || fail "all_units_done: should return true for all-complete file"

# all_units_done — missing file → false
all_units_done "/tmp/no-such-file-$$" \
  && fail "all_units_done: should return false for missing file" \
  || pass "all_units_done: correctly false for missing file"

# populate: the progress template's placeholder line is not real progress
TEMPLATE_PROGRESS="$TEST_DIR/template-progress.md"
printf -- '# CR\n\n- [ ] U001 (fill in after running plan mode)\n' > "$TEMPLATE_PROGRESS"
populate_progress_from_plan "$FAKE_PLAN" "$TEMPLATE_PROGRESS"
check_contains "populate: replaces the template placeholder line" "U003 Implement CRUD endpoints" "$(cat "$TEMPLATE_PROGRESS")"

# populate: priority and size tags come from the unit's own fields
TAGGED_PLAN="$TEST_DIR/tagged-plan.md"
printf -- '## Unit: Alpha (ID: U001)\n**Priority:** P1-High\n**Size Estimate:** Medium\n\n## Unit: Beta (ID: U002)\n**Priority:** P0-Critical\n' > "$TAGGED_PLAN"
printf '# stub\n' > "$STUB_PROGRESS"
populate_progress_from_plan "$TAGGED_PLAN" "$STUB_PROGRESS"
check_contains "populate: priority|size tag" "U001 \[P1[|]M\] Alpha" "$(cat "$STUB_PROGRESS")"
check_contains "populate: priority-only tag" "U002 \[P0\] Beta"    "$(cat "$STUB_PROGRESS")"

# select_next_unit: in-progress first, then priority, bold IDs, blocked skipped
SEL="$TEST_DIR/select-progress.md"
printf -- '- [x] U001 Done\n- [!] U002 Blocked\n- [ ] U003 [P2] Later\n- [ ] **U004 [P0] Urgent**\n' > "$SEL"
check_contains "select: highest priority, bold ID" "^U004$" "$(select_next_unit "$SEL")"
printf -- '- [ ] U005 [P0] Urgent\n- [~] U006 Half done\n' > "$SEL"
check_contains "select: resumes the in-progress unit first" "^U006$" "$(select_next_unit "$SEL")"
printf -- '  - [ ] U0nn sample line in a comment\n- [x] U001 Done\n' > "$SEL"
check_contains "select: nothing left → empty" "^$" "$(select_next_unit "$SEL")"

# extract_unit_section: only the selected unit's entry
SECTION_PLAN="$TEST_DIR/section-plan.md"
printf -- '## Unit: One (ID: U001)\nfirst body\n\n## Unit: Two (ID: U002)\nsecond body\n### Test Plan\nsecond tests\n\n## Unit: Three (ID: U003)\nthird body\n' > "$SECTION_PLAN"
SECTION=$(extract_unit_section "$SECTION_PLAN" U002)
check_contains     "extract: has its own body"      "second body"  "$SECTION"
check_contains     "extract: keeps its subsections" "second tests" "$SECTION"
check_not_contains "extract: excludes later units"  "third body"   "$SECTION"
check_not_contains "extract: excludes earlier units" "first body"  "$SECTION"

# ──────────────────────────────────────────────────────────────────────────────
section "11. lib/tui.sh — fallback mode (RALPH_NO_TUI=1)"
# ──────────────────────────────────────────────────────────────────────────────
source "$RALPH_CLI/lib/tui.sh"

# tui_available should be false
tui_available \
  && fail "tui_available: should be false with RALPH_NO_TUI=1" \
  || pass "tui_available: correctly false with RALPH_NO_TUI=1"

# tui_progress
OUT=$(tui_progress 7 20)
check_contains "tui_progress: shows correct counts" "7/20"  "$OUT"
check_contains "tui_progress: shows percentage"     "35%"   "$OUT"
check_contains "tui_progress: renders bar chars"    "█|░"   "$OUT"

OUT=$(tui_progress 20 20)
check_contains "tui_progress: 100% renders full bar" "20/20" "$OUT"
check_contains "tui_progress: 100% percentage"       "100%"  "$OUT"

OUT=$(tui_progress 0 10)
check_contains "tui_progress: 0% renders empty bar" "0/10" "$OUT"

# tui_stage_badge
for stage in "1-write-spec" "2-plan" "3-ready-to-build" "3-build" "4-blocked" "5-done"; do
  OUT=$(tui_stage_badge "$stage")
  check_contains "tui_stage_badge: $stage renders non-empty" "." "$OUT"
done


# tui_log functions (just check they don't crash)
tui_log_ok     "test ok message"     2>/dev/null && pass "tui_log_ok runs"     || fail "tui_log_ok crashed"
tui_log_warn   "test warn message"   2>/dev/null && pass "tui_log_warn runs"   || fail "tui_log_warn crashed"
tui_log_err    "test err message"    2>/dev/null && pass "tui_log_err runs"    || fail "tui_log_err crashed"
tui_log_create "test create message" 2>/dev/null && pass "tui_log_create runs" || fail "tui_log_create crashed"
tui_log_skip   "test skip message"   2>/dev/null && pass "tui_log_skip runs"   || fail "tui_log_skip crashed"


# ──────────────────────────────────────────────────────────────────────────────
section "12. Placeholder resolution — build prompt content"
# ──────────────────────────────────────────────────────────────────────────────
BUILD="development/cr1/prompts/build.md"
check_contains "build.md: CR name present"         "CR1"          "$(cat "$BUILD")"
check_contains "build.md: CR title present"        "Task CRUD API" "$(cat "$BUILD")"
check_contains "build.md: spec path correct"       "development/cr1/spec/spec.md" "$(cat "$BUILD")"
check_contains "build.md: plan path correct"       "development/cr1/plan"         "$(cat "$BUILD")"
check_contains "build.md: progress path correct"   "development/cr1/progress"     "$(cat "$BUILD")"
check_contains "build.md: AGENTS.md referenced"    "AGENTS.md"    "$(cat "$BUILD")"
check_contains "build.md: critical rules present"  "exactly one unit" "$(cat "$BUILD")"
check_not_contains "build.md: no 'fix unrelated failures' instruction" "including unrelated pre-existing" "$(cat "$BUILD")"
check_contains "build.md: completion gate present" "Completion gate|sign-off"    "$(cat "$BUILD")"
check_contains "build.md: loop state note present" "Loop state"   "$(cat "$BUILD")"


# ──────────────────────────────────────────────────────────────────────────────
section "13. spec-planning.md — key enhancements present"
# ──────────────────────────────────────────────────────────────────────────────
PLAN_PROMPT="development/cr1/prompts/spec-planning.md"
check_contains "spec-planning: AGENTS.md read step"     "AGENTS.md"              "$(cat "$PLAN_PROMPT")"
check_contains "spec-planning: unit count guardrail"    "8.20 units|8–20 units|8-20 units" "$(cat "$PLAN_PROMPT")"
check_contains "spec-planning: parallel bundles section" "Parallel Bundles"      "$(cat "$PLAN_PROMPT")"
check_contains "spec-planning: rollback plan field"     "Rollback Plan"          "$(cat "$PLAN_PROMPT")"
check_contains "spec-planning: exactly 5 synthesis"     "exactly 5"              "$(cat "$PLAN_PROMPT")"
check_contains "spec-planning: EXISTING_CODE=true"      "true"                   "$(cat "$PLAN_PROMPT")"


# ──────────────────────────────────────────────────────────────────────────────
section "14. ralph critique — graceful error (no plan)"
# ──────────────────────────────────────────────────────────────────────────────
OUT=$(bash "$RALPH" critique --cr 99 2>&1 || true)
check_contains "critique fails gracefully with no plan" "No plan file found|plan.*not found|run.*plan" "$OUT"


# ──────────────────────────────────────────────────────────────────────────────
section "15. ralph start — prompt file not found error"
# ──────────────────────────────────────────────────────────────────────────────
OUT=$(bash "$RALPH" start --mode build --cr 99 2>&1 || true)
check_contains "start fails gracefully for missing CR" "not found|add-cr" "$OUT"


# ──────────────────────────────────────────────────────────────────────────────
section "16. ralph add-cr — all 8 implementation types scaffold correctly"
# ──────────────────────────────────────────────────────────────────────────────
N=10
for TYPE in api database infrastructure refactor; do
  OUT=$(printf 'n\n' | bash "$RALPH" add-cr \
    --number $N --title "Test $TYPE" --type "$TYPE" 2>&1 || true)
  check_file "development/cr${N}/prompts/build.md" "cr${N} ($TYPE) build.md"
  check_no_placeholders "development/cr${N}/prompts/build.md"
  N=$((N+1))
done


# ──────────────────────────────────────────────────────────────────────────────
section "17. ralph start — bounded, hermetic, one-unit iterations (fake claude)"
# ──────────────────────────────────────────────────────────────────────────────
# A fake `claude` records its arguments, CLAUDE_CODE_* environment and stdin,
# then prints scripted stream-json. FAKE_CLAUDE_MODE picks the outcome.
FAKEBIN="$TEST_DIR/fakebin"
CALLS="$TEST_DIR/fake-calls"
mkdir -p "$FAKEBIN" "$CALLS"
cat > "$FAKEBIN/claude" << 'EOF'
#!/usr/bin/env bash
out="${FAKE_CLAUDE_DIR:?}"
n=$(( $(cat "$out/count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$out/count"
printf '%s\n' "$@" > "$out/args.$n"
env | grep -E '^CLAUDE_CODE_' | sort > "$out/env.$n"
cat > "$out/stdin.$n"
case "${FAKE_CLAUDE_MODE:-ok}" in
  ok)
    echo '{"type":"system","subtype":"init","session_id":"s1"}'
    echo '{"type":"assistant","message":{"id":"m1","usage":{"input_tokens":10,"cache_read_input_tokens":40000,"cache_creation_input_tokens":2000},"content":[{"type":"tool_use","name":"Agent","input":{}}]}}'
    echo '{"type":"assistant","message":{"id":"m2","usage":{"input_tokens":5,"cache_read_input_tokens":90000,"cache_creation_input_tokens":1000},"content":[{"type":"tool_use","name":"Bash","input":{}}]}}'
    echo '{"type":"system","subtype":"compact_boundary"}'
    echo '{"type":"result","subtype":"success","is_error":false,"num_turns":7,"duration_ms":120000,"duration_api_ms":90000,"total_cost_usd":1.25,"session_id":"s1"}'
    exit 0 ;;
  cap)
    echo '{"type":"result","subtype":"error_max_turns","is_error":true,"num_turns":200,"duration_ms":1000,"total_cost_usd":3.5}'
    exit 1 ;;
  fail)
    echo 'API Error: 529 overloaded'
    exit 1 ;;
esac
EOF
chmod +x "$FAKEBIN/claude"
fake_calls() { cat "$CALLS/count" 2>/dev/null || echo 0; }

LOOP_DIR="$TEST_DIR/loop-project"
mkdir -p "$LOOP_DIR/development/cr1/"{prompts,plan,progress,spec}
cd "$LOOP_DIR"
git init -q . && git -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
echo "BUILD PROMPT BODY" > development/cr1/prompts/build.md
echo "PLAN PROMPT BODY"  > development/cr1/prompts/spec-planning.md
echo "spec text"         > development/cr1/spec/spec.md
printf -- '## Unit: First (ID: U001)\nfirst body\n\n## Unit: Second (ID: U002)\nsecond body\n\n## Unit: Third (ID: U003)\nthird body\n' \
  > development/cr1/plan/cr1-implementation-plan.md
printf -- '- [x] U001 First\n- [ ] U002 Second\n- [ ] U003 Third\n' > development/cr1/progress/implementation-progress.md
echo '{"mcpServers":{"personal":{"url":"http://localhost:1/mcp"}}}' > .mcp.json   # untracked, like an editor extension's

run_start() { PATH="$FAKEBIN:$PATH" FAKE_CLAUDE_DIR="$CALLS" RALPH_RETRY_DELAY=0 bash "$RALPH" start "$@" 2>&1; }

OUT=$(run_start --cr 1 --max 1); STATUS=$?
N=$(fake_calls)
ARGS=$(tr '\n' ' ' < "$CALLS/args.$N")
check_contains "build: exits cleanly"                    "^0$" "$STATUS"
check_contains "build: turn and budget caps"             "--max-turns 200 --max-budget-usd 30" "$ARGS"
check_contains "build: project-only MCP servers"         "--strict-mcp-config --mcp-config" "$ARGS"
check_not_contains "build: untracked .mcp.json not used"  "--mcp-config \.mcp\.json" "$ARGS"
check_contains "build: project and local settings only"  "--setting-sources project,local" "$ARGS"
check_contains "build: explorer subagent"                "--agents .*explorer.json" "$ARGS"
check_contains "build: one-unit contract"                "--append-system-prompt" "$ARGS"
check_contains "build: compaction window 200k"           "CLAUDE_CODE_AUTO_COMPACT_WINDOW=200000" "$(cat "$CALLS/env.$N")"
check_contains "build: auto memory off"                  "CLAUDE_CODE_DISABLE_AUTO_MEMORY=1" "$(cat "$CALLS/env.$N")"
PROMPT=$(cat "$CALLS/stdin.$N")
check_contains     "build: names the iteration's unit"   "This iteration's unit: U002" "$PROMPT"
check_contains     "build: includes that unit's entry"   "second body" "$PROMPT"
check_not_contains "build: leaves out other units"       "third body"  "$PROMPT"
check_contains     "build: keeps the build prompt"       "BUILD PROMPT BODY" "$PROMPT"
check_contains     "build: next unit shown without its checkbox" "later iteration, not now\): U003 Third" "$PROMPT"
INDEX=$(tail -n 1 log/index.jsonl 2>/dev/null)
check_contains "index: records the unit"                 '"unit":"U002"' "$INDEX"
check_contains "index: records peak context"             '"peak_context":91005' "$INDEX"
check_contains "index: records compactions"              '"compactions":1' "$INDEX"
check_contains "index: records subagent calls"           '"subagent_calls":1' "$INDEX"
check_contains "build: prints the iteration summary"     "U002 · 7 turns · 2 min" "$OUT"
check_not_contains "build: no push without --push"       "Creating remote|git push" "$OUT"

BEFORE=$(fake_calls)
OUT=$(run_start --cr 1 --dry-run)
check_contains "dry run: never calls claude"             "^${BEFORE}$" "$(fake_calls)"
check_contains "dry run: shows the unit and size"        "U002" "$OUT"
check_contains "dry run: compares with the old prompt"   "tokens" "$OUT"

BEFORE=$(fake_calls)
OUT=$(run_start --mode plan --cr 1)
check_contains "plan: one iteration by default"          "^$((BEFORE + 1))$" "$(fake_calls)"
check_contains "plan: larger caps for one long session"  "--max-turns 300 --max-budget-usd 60" "$(tr '\n' ' ' < "$CALLS/args.$(fake_calls)")"

BEFORE=$(fake_calls)
OUT=$(FAKE_CLAUDE_MODE=cap run_start --cr 1 --max 2); STATUS=$?
check_contains "cap hit: loop continues"                 "^$((BEFORE + 2))$" "$(fake_calls)"
check_contains "cap hit: explained, not a failure"       "hit its cap" "$OUT"
check_contains "cap hit: exits cleanly"                  "^0$" "$STATUS"

BEFORE=$(fake_calls)
OUT=$(FAKE_CLAUDE_MODE=fail run_start --cr 1 --max 5)
check_contains "failures: stops after 3 in a row"        "^$((BEFORE + 3))$" "$(fake_calls)"
check_contains "failures: says why it stopped"           "failed 3 times in a row" "$OUT"

OUT=$(run_start --cr || true)
check_contains "args: missing value is a clear error"    "needs a value" "$OUT"
OUT=$(run_start --cr 1 --max abc || true)
check_contains "args: non-numeric --max is a clear error" "whole number" "$OUT"

cd "$TEST_DIR"


# ──────────────────────────────────────────────────────────────────────────────
# SUMMARY
# ──────────────────────────────────────────────────────────────────────────────
TOTAL=$((PASS + FAIL + SKIP))
printf "\n${B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}\n"
printf "${B}  Results: %d/%d passed" "$PASS" "$((PASS+FAIL))"
[[ $SKIP -gt 0 ]] && printf "  (%d skipped)" "$SKIP"
printf "${X}\n"
[[ $FAIL -gt 0 ]] && printf "${R}  %d test(s) failed${X}\n" "$FAIL"
[[ $FAIL -eq 0 ]] && printf "${G}  All tests passed${X}\n"
printf "${B}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${X}\n\n"
printf "  Test dir preserved at: %s\n\n" "$TEST_DIR"

[[ $FAIL -eq 0 ]] && exit 0 || exit 1
