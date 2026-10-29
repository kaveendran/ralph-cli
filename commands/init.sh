#!/usr/bin/env bash
# ralph init — scaffold the Ralph Loop pattern into a project directory.
# Supports two modes:
#   auto   — gathers project info, runs Claude once to generate initial spec + brainstorm
#   manual — creates scaffold only; user writes the spec themselves
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/template.sh"
source "$RALPH_DIR/lib/tui.sh"
source "$RALPH_DIR/lib/rules.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
PROJECT_DIR="."
FORCE_MODE=""  # "auto" or "manual" — skips the mode prompt if set

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project-dir) PROJECT_DIR="$2"; shift 2 ;;
    --auto)        FORCE_MODE="auto";   shift ;;
    --manual)      FORCE_MODE="manual"; shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

cd "$PROJECT_DIR"
PROJECT_DIR="$(pwd)"

tui_header "init — bootstrap Ralph Loop" "$PROJECT_DIR"

# ── Core project info (both modes) ───────────────────────────────────────────
DEFAULT_NAME="$(basename "$PROJECT_DIR")"
PROJECT_NAME="$(tui_input "Project name" "$DEFAULT_NAME")"
PROJECT_NAME="${PROJECT_NAME:-$DEFAULT_NAME}"

PROJECT_DESCRIPTION="$(tui_input "One-line description")"
PROJECT_DESCRIPTION="${PROJECT_DESCRIPTION:-AI-powered project}"

echo ""

# ── Mode selection ────────────────────────────────────────────────────────────
if [[ -z "$FORCE_MODE" ]]; then
  _mode_choice="$(tui_choose "Setup mode" \
    "Auto — Claude generates the initial spec from your brainstorm" \
    "Manual — create scaffold only; you write the spec yourself")"
  case "$_mode_choice" in
    Auto*)   FORCE_MODE="auto"   ;;
    Manual*) FORCE_MODE="manual" ;;
    *)       FORCE_MODE="auto"   ;;
  esac
fi

# ── Shared helpers ────────────────────────────────────────────────────────────
CREATED=0
SKIPPED=0

create_file() {
  local dst="$1" src="$2"
  shift 2
  if [[ -f "$dst" ]]; then
    tui_log_skip "$dst (already exists)"
    SKIPPED=$((SKIPPED + 1))
    return
  fi
  mkdir -p "$(dirname "$dst")"
  apply_template "$src" "$dst" "$@"
  tui_log_create "$dst"
  CREATED=$((CREATED + 1))
}

create_dir() {
  local d="$1"
  [[ -d "$d" ]] && return
  mkdir -p "$d"
  echo "  MKDIR  $d"
}

# ── Implementation type selection (used in both modes) ────────────────────────
pick_impl_type() {
  tui_choose "Implementation type" \
    "backend+frontend" \
    "backend" \
    "frontend" \
    "feature"
}

# ── Build test/tech blocks from implementation type ───────────────────────────
build_test_cmds() {
  local t="$1"
  local has_infra="${2:-false}"
  case "$t" in
    backend+frontend)
      if [[ "$has_infra" == "true" ]]; then
        printf 'Run tests in three layers after every unit:\n\n**Layer 1 — Unit tests** (always, no Docker — fast feedback):\n- `bash scripts/test-unit.sh`\n\n**Layer 2 — API integration tests** (after any backend unit):\n- `bash scripts/test-api.sh`\n  Starts `test-db` + `backend-test` in Docker, runs `backend/tests/api/`, tears down with `down -v`.\n\n**Layer 3 — E2E / UI tests** (after any frontend unit, or when Layer 2 passes):\n- `bash scripts/test-e2e.sh`\n  Full Docker stack (test-db → backend-test → frontend-test). Playwright tests in `e2e/tests/`.\n  For an interactive Playwright MCP session: `bash scripts/test-e2e.sh --up`, then use the playwright MCP browser tools against http://localhost:5174, then `docker compose -f docker-compose.test.yml down -v`.\n\nAll three layers must be green before marking a unit `[x]` complete.'
      else
        echo "- **Backend:** \`cd backend && python -m pytest -p no:cacheprovider --no-cov -q\`
- **Backend + coverage:** \`cd backend && python -m pytest --cov=app --cov-report=term-missing\`
- **Frontend:** \`cd frontend && npx vitest run\`
- **TypeScript check:** \`cd frontend && npx tsc --noEmit\`"
      fi ;;
    backend)
      if [[ "$has_infra" == "true" ]]; then
        printf 'Run tests in two layers after every unit:\n\n**Layer 1 — Unit tests** (always, no Docker — fast feedback):\n- `bash scripts/test-unit.sh`\n\n**Layer 2 — API integration tests** (after any backend unit):\n- `bash scripts/test-api.sh`\n  Starts `test-db` + `backend-test` in Docker, runs `backend/tests/api/`, tears down with `down -v`.\n\nBoth layers must be green before marking a unit `[x]` complete.'
      else
        echo "- **Backend:** \`cd backend && python -m pytest -p no:cacheprovider --no-cov -q\`
- **Backend + coverage:** \`cd backend && python -m pytest --cov=app --cov-report=term-missing\`"
      fi ;;
    frontend)
      echo "- **Frontend:** \`cd frontend && npx vitest run\`
- **TypeScript check:** \`cd frontend && npx tsc --noEmit\`" ;;
    *)
      echo "- (add test commands for your stack here)" ;;
  esac
}

build_tech_constraints() {
  local t="$1" stack="$2"
  if [[ -n "$stack" ]]; then
    echo "- $stack
- No new dependencies without user confirmation."
  else
    case "$t" in
      backend+frontend)
        echo "- Backend: Python 3.11+, FastAPI, SQLAlchemy, Pydantic, pytest.
- Frontend: TypeScript 5+, React 19, Vite, Tailwind CSS, Vitest.
- No new dependencies without user confirmation." ;;
      backend)
        echo "- Python 3.11+, FastAPI, SQLAlchemy, Pydantic, pytest + pytest-asyncio.
- Alembic for all schema migrations — no raw SQL.
- No new dependencies without user confirmation." ;;
      frontend)
        echo "- TypeScript 5+, React 19, Vite, Tailwind CSS, Vitest + React Testing Library.
- All colors via CSS variables / Tailwind utilities — no raw hex.
- No new dependencies without user confirmation." ;;
      *)
        echo "- (list your tech stack constraints here)" ;;
    esac
  fi
}

# ── AUTO MODE ─────────────────────────────────────────────────────────────────
bootstrap_auto() {
  tui_header "Auto mode — Claude will generate your initial spec"

  IMPL_TYPE="$(pick_impl_type)"
  echo ""

  TECH_STACK="$(tui_input "Tech stack (e.g. Python/FastAPI, React/TypeScript, PostgreSQL)")"
  TECH_STACK="${TECH_STACK:-to be determined}"
  echo ""

  BRAINSTORM="$(tui_input "Core goals and features — describe your idea freely")"
  BRAINSTORM="${BRAINSTORM:-to be defined}"
  echo ""

  CONSTRAINTS="$(tui_input "Constraints or non-negotiables (leave blank if none)")"
  echo ""

  # ── Build the spec generation prompt ────────────────────────────────────────
  SPEC_PROMPT="You are a senior software architect bootstrapping a new project.

Based on the information below, generate a comprehensive technical specification document.

PROJECT NAME: ${PROJECT_NAME}
DESCRIPTION: ${PROJECT_DESCRIPTION}
TECH STACK: ${TECH_STACK}
IMPLEMENTATION TYPE: ${IMPL_TYPE}
CORE GOALS AND FEATURES:
${BRAINSTORM}
CONSTRAINTS: ${CONSTRAINTS:-none}

Write a technical specification with all of these sections. Be specific, opinionated, and actionable — this spec will drive autonomous AI coding loops, so precision matters. Avoid vague statements. Use concrete technology choices and measurable criteria.

## 1. Project Overview
2–3 paragraphs: what the system does, its primary value, key users.

## 2. System Architecture
High-level architecture. Main components, how they communicate, key design decisions. Include a simple ASCII diagram if helpful.

## 3. Technology Stack
Each chosen technology with a one-line rationale. Include versions where relevant.

## 4. Functional Requirements
Organized by feature area. Each requirement numbered (FR-001, FR-002 ...) and specific enough to be testable.

## 5. Non-Functional Requirements
Performance targets, security requirements, scalability approach, reliability/uptime goals.

## 6. Key Workflows
3–5 end-to-end workflows with step-by-step breakdowns (e.g. 'User submits loan application: 1. User fills form → 2. System validates → 3. Agent processes ...'). These become acceptance test scenarios.

## 7. Data Model
Key entities, their fields, and relationships. Storage technology per entity if mixed (e.g. Postgres for relational, Redis for cache).

## 8. API Design
Key endpoints with method, path, request body shape, response shape, and auth requirements. Group by domain.

## 9. Acceptance Criteria
Top-level measurable criteria that define 'done' for the initial implementation. Numbered AC-001, AC-002 ...

## 10. Open Questions and Assumptions
Things that need clarification. List as: 'Q: ... / Assumption: ...'

## 11. Suggested Implementation Phases
3–5 phases for incremental delivery. Each phase: what it delivers and why it comes before the next.

Output only the specification document. No preamble, no explanation outside the document."

  # ── Check claude is available ────────────────────────────────────────────────
  if ! command -v claude &>/dev/null; then
    echo "  ERROR: 'claude' CLI not found in PATH."
    echo "  Install it with: npm install -g @anthropic-ai/claude-code"
    echo "  Falling back to manual mode — spec stub created instead."
    echo ""
    return 1
  fi

  mkdir -p development/initial/spec
  local spec_file="development/initial/spec/spec.md"
  local tmp_prompt exit_code=0
  tmp_prompt="$(mktemp)"
  printf '%s' "$SPEC_PROMPT" > "$tmp_prompt"

  tui_spin "Generating initial specification with Claude (~30s)" \
    bash -c "claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      < \"$tmp_prompt\" > \"$spec_file\"" \
    || exit_code=$?
  rm -f "$tmp_prompt"

  if [[ $exit_code -ne 0 ]] || [[ ! -s "$spec_file" ]]; then
    tui_log_warn "Claude spec generation failed (exit $exit_code) — stub spec created instead."
    # Write fallback stub so the file exists
    apply_template "$RALPH_DIR/templates/spec.md" "$spec_file" \
      "CR_NAME" "Initial" \
      "CR_TITLE" "$PROJECT_NAME" \
      "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
      "CREATED_DATE" "$(date +%Y-%m-%d)"
  else
    tui_log_create "$spec_file (AI-generated)"
    CREATED=$((CREATED + 1))
  fi

  echo ""
  tui_log_ok "Review your spec: $spec_file"
  echo ""

  # ── Second Claude pass — derive a real CLAUDE.md from the just-generated spec ──
  if [[ -s "$spec_file" ]]; then
    local claude_md_prompt
    claude_md_prompt="You are writing CLAUDE.md for an AI coding agent working on a new project.

CLAUDE.md is loaded into the agent's context on every invocation. It must capture project standards, conventions, and non-obvious decisions — NOT operational commands (those go in AGENTS.md) and NOT product description (that's in spec.md).

Read the project spec, tech stack, and brainstorm below, then write a CLAUDE.md with these sections:

# Project: ${PROJECT_NAME}

${PROJECT_DESCRIPTION}

## Tech Stack
(One line per technology — name, version constraint, why chosen. Pull from the spec where possible.)

## Architecture Principles
(3–6 bullet points capturing the load-bearing decisions: layering, async vs sync, state management approach, data access pattern. Extract from spec §2 System Architecture.)

## Code Standards
(Concrete, enforceable rules: naming, file layout, type usage, error handling, logging. If frontend: color/CSS rules, component patterns. If backend: schema/migration rules, async rules.)

## Testing Standards
(Required test types per change, fixture conventions, what 'green' means.)

## Out-of-bounds
(Things this project deliberately does NOT do — anti-patterns, rejected approaches. Pull from spec §10 Open Questions / Assumptions if present.)

Rules:
- Be opinionated and specific. Avoid 'follow best practices'.
- No placeholder text. If you don't know something, omit the bullet rather than write '(TBD)'.
- Total length ~50–120 lines. This file is read every iteration; brevity matters.

---
SPEC:
$(cat "$spec_file")
---
TECH STACK: ${TECH_STACK}
BRAINSTORM: ${BRAINSTORM}
CONSTRAINTS: ${CONSTRAINTS:-none}

Output only the CLAUDE.md document. No preamble."

    local tmp_claude_prompt
    tmp_claude_prompt="$(mktemp)"
    printf '%s' "$claude_md_prompt" > "$tmp_claude_prompt"

    local claude_md_exit=0
    tui_spin "Generating CLAUDE.md from spec (~20s)" \
      bash -c "claude -p --dangerously-skip-permissions --output-format text --model sonnet \
        < \"$tmp_claude_prompt\" > CLAUDE.md.generated" \
      || claude_md_exit=$?
    rm -f "$tmp_claude_prompt"

    if [[ $claude_md_exit -eq 0 ]] && [[ -s CLAUDE.md.generated ]]; then
      if [[ -f CLAUDE.md ]] && grep -q '(list your tech stack here)' CLAUDE.md 2>/dev/null; then
        # Existing CLAUDE.md is the stub — replace it
        mv CLAUDE.md.generated CLAUDE.md
        tui_log_create "CLAUDE.md (AI-generated from spec)"
      elif [[ ! -f CLAUDE.md ]]; then
        mv CLAUDE.md.generated CLAUDE.md
        tui_log_create "CLAUDE.md (AI-generated from spec)"
      else
        # User has a custom CLAUDE.md — keep theirs, leave generated as .suggested
        mv CLAUDE.md.generated CLAUDE.md.suggested
        tui_log_warn "CLAUDE.md exists with custom content — generated version saved as CLAUDE.md.suggested"
      fi
    else
      rm -f CLAUDE.md.generated
      tui_log_warn "CLAUDE.md generation skipped (claude returned non-zero or empty output)"
    fi
  fi
}

# ── MANUAL MODE ───────────────────────────────────────────────────────────────
bootstrap_manual() {
  echo "Manual mode — a spec stub will be created for you to fill in."
  echo ""
  IMPL_TYPE="$(pick_impl_type)"
  TECH_STACK=""
  echo ""
}

# ── Run the chosen mode ───────────────────────────────────────────────────────
IMPL_TYPE="feature"
TECH_STACK=""

if [[ "$FORCE_MODE" == "auto" ]]; then
  bootstrap_auto || {
    # Fallback: ask for impl type if auto failed
    IMPL_TYPE="$(pick_impl_type)"
    TECH_STACK=""
  }
else
  bootstrap_manual
fi

CREATED_DATE="$(date +%Y-%m-%d)"
# ── Offer Docker test infrastructure (backend types only) ─────────────────────
GENERATE_TEST_INFRA=false
if [[ "$IMPL_TYPE" == "backend" || "$IMPL_TYPE" == "backend+frontend" ]]; then
  echo ""
  echo "Docker test infrastructure generates docker-compose.test.yml + scripts/"
  echo "for isolated Layer 1 (unit), Layer 2 (API), and Layer 3 (E2E) testing."
  tui_confirm "Generate Docker test infrastructure?" && GENERATE_TEST_INFRA=true || true
fi

TEST_CMDS="$(build_test_cmds "$IMPL_TYPE" "$GENERATE_TEST_INFRA")"
TECH_CONSTRAINTS="$(build_tech_constraints "$IMPL_TYPE" "$TECH_STACK")"

CRITICAL_RULES="$(build_layered_rules "$IMPL_TYPE")"

# ── Create scaffold files ─────────────────────────────────────────────────────
tui_section "Creating scaffold"

create_file "AGENTS.md" "$RALPH_DIR/templates/agents.md" \
  "PROJECT_NAME" "$PROJECT_NAME"

create_file "CLAUDE.md" "$RALPH_DIR/templates/claude.md" \
  "PROJECT_NAME" "$PROJECT_NAME" \
  "PROJECT_DESCRIPTION" "$PROJECT_DESCRIPTION"

create_file "loop_plan.sh" "$RALPH_DIR/templates/loop_plan.sh"
[[ -f "loop_plan.sh" ]] && chmod +x "loop_plan.sh"

create_file "loop_cr.sh" "$RALPH_DIR/templates/loop_cr.sh"
[[ -f "loop_cr.sh" ]] && chmod +x "loop_cr.sh"

create_dir "development/shared"
create_dir "development/initial/spec"
create_dir "development/initial/plan"
create_dir "development/initial/progress"

# ── Test infrastructure (backend / backend+frontend only) ─────────────────────
if [[ "$GENERATE_TEST_INFRA" == "true" ]]; then
  create_dir "scripts"
  create_dir "e2e/tests"
  create_dir ".claude"
  create_file "docker-compose.test.yml"  "$RALPH_DIR/templates/docker-compose.test.yml"
  create_file "scripts/test-unit.sh"     "$RALPH_DIR/templates/test-unit.sh"
  create_file "scripts/test-api.sh"      "$RALPH_DIR/templates/test-api.sh"
  [[ -f "scripts/test-unit.sh" ]] && chmod +x "scripts/test-unit.sh"
  [[ -f "scripts/test-api.sh"  ]] && chmod +x "scripts/test-api.sh"
  if [[ "$IMPL_TYPE" == "backend+frontend" ]]; then
    create_file "scripts/test-e2e.sh"       "$RALPH_DIR/templates/test-e2e.sh"
    create_file "e2e/playwright.config.ts"  "$RALPH_DIR/templates/playwright.config.ts"
    create_file ".claude/settings.json"     "$RALPH_DIR/templates/claude-settings.json"
    [[ -f "scripts/test-e2e.sh" ]] && chmod +x "scripts/test-e2e.sh"
  fi
fi
create_dir "development/initial/prompts"

create_file "development/initial/progress/implementation-progress.md" "$RALPH_DIR/templates/progress.md" \
  "CR_NAME" "Initial" \
  "CR_TITLE" "$PROJECT_NAME"

create_file "development/templates/implementation-planning.md" "$RALPH_DIR/templates/spec-planning.md" \
  "SPEC_PATH" "development/initial/spec/spec.md" \
  "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
  "PLAN_PATH" "development/initial/plan/full-implementation-plan.md" \
  "EXISTING_CODE" "false" \
  "SPECIAL_NOTES" "(fill in before running plan mode)"

create_file "development/initial/prompts/build.md" "$RALPH_DIR/templates/build.md" \
  "CR_NAME" "Initial" \
  "CR_TITLE" "Initial Implementation" \
  "SPEC_PATH" "development/initial/spec/spec.md" \
  "PLAN_PATH" "development/initial/plan/full-implementation-plan.md" \
  "PROGRESS_PATH" "development/initial/progress/implementation-progress.md" \
  "PRIOR_CR_LINE" "" \
  "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
  "CRITICAL_RULES" "$CRITICAL_RULES" \
  "TEST_COMMANDS" "$TEST_CMDS" \
  "TECH_CONSTRAINTS" "$TECH_CONSTRAINTS" \
  "GIT_TAG" "v0.1.0" \
  "SPECIAL_NOTES" "(none)" \
  "CREATED_DATE" "$CREATED_DATE"

create_file "development/initial/prompts/spec-planning.md" "$RALPH_DIR/templates/spec-planning.md" \
  "SPEC_PATH" "development/initial/spec/spec.md" \
  "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
  "PLAN_PATH" "development/initial/plan/full-implementation-plan.md" \
  "EXISTING_CODE" "false" \
  "SPECIAL_NOTES" "(none)"

# In manual mode, create the spec stub if it doesn't exist yet
if [[ "$FORCE_MODE" == "manual" ]]; then
  create_file "development/initial/spec/spec.md" "$RALPH_DIR/templates/spec.md" \
    "CR_NAME" "Initial" \
    "CR_TITLE" "$PROJECT_NAME" \
    "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
    "CREATED_DATE" "$CREATED_DATE"
fi

# ── Summary ───────────────────────────────────────────────────────────────────
tui_header "Done" "$CREATED created, $SKIPPED skipped"

if [[ "$FORCE_MODE" == "auto" ]]; then
  echo "Generated spec: development/initial/spec/spec.md"
  echo "  Review it, adjust anything that's off, then:"
  echo ""
fi

echo "Next steps:"
echo "  1. Review / edit: development/initial/spec/spec.md"
echo "  2. ralph start --mode plan       (Claude generates implementation plan)"
echo "  3. ralph start --mode build      (Claude implements the plan)"
echo "  4. For subsequent change requests: ralph add-cr"
echo ""

# ── Offer to kick off planning loop immediately (auto mode only) ──────────────
if [[ "$FORCE_MODE" == "auto" ]]; then
  if tui_confirm "Run the planning loop now to generate the implementation plan?" no; then
    echo ""
    bash "$RALPH_DIR/commands/start.sh" --mode plan \
      --prompt "development/initial/prompts/spec-planning.md"
  fi
fi
