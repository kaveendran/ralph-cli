#!/usr/bin/env bash
# ralph add-cr — scaffold a new change request under development/crN/
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/template.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
CR_NUMBER=""
CR_TITLE=""
IMPL_TYPE=""
PRIOR_CR=""
SPEC_SRC=""
SPECIAL_NOTES="(none)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --number)    CR_NUMBER="$2";   shift 2 ;;
    --title)     CR_TITLE="$2";    shift 2 ;;
    --type)      IMPL_TYPE="$2";   shift 2 ;;
    --prior-cr)  PRIOR_CR="$2";    shift 2 ;;
    --spec-file) SPEC_SRC="$2";    shift 2 ;;
    --notes)     SPECIAL_NOTES="$2"; shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ralph add-cr — scaffold a new CR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── Interactive prompts for missing args ──────────────────────────────────────
if [[ -z "$CR_NUMBER" ]]; then
  read -rp "CR number (e.g. 22): " CR_NUMBER
fi
if [[ -z "$CR_NUMBER" ]] || ! [[ "$CR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "Error: CR number must be a positive integer."
  exit 1
fi

if [[ -z "$CR_TITLE" ]]; then
  read -rp "CR title (short description): " CR_TITLE
fi
if [[ -z "$CR_TITLE" ]]; then
  CR_TITLE="CR${CR_NUMBER} Implementation"
fi

if [[ -z "$IMPL_TYPE" ]]; then
  echo "Implementation type options: backend | frontend | backend+frontend | feature | api | database | infrastructure | refactor"
  read -rp "Implementation type [feature]: " IMPL_TYPE
  IMPL_TYPE="${IMPL_TYPE:-feature}"
fi

if [[ -z "$PRIOR_CR" ]]; then
  read -rp "Prior CR number to reference as baseline (leave blank if none): " PRIOR_CR
fi

if [[ "$SPECIAL_NOTES" == "(none)" ]]; then
  read -rp "Special notes for build prompt (leave blank for none): " NOTES_INPUT
  [[ -n "$NOTES_INPUT" ]] && SPECIAL_NOTES="$NOTES_INPUT"
fi

# ── Derived paths ─────────────────────────────────────────────────────────────
CR_DIR="development/cr${CR_NUMBER}"
SPEC_PATH="${CR_DIR}/spec/spec.md"
PLAN_PATH="${CR_DIR}/plan/cr${CR_NUMBER}-implementation-plan.md"
PROGRESS_PATH="${CR_DIR}/progress/implementation-progress.md"
BUILD_PROMPT="${CR_DIR}/prompts/build.md"
PLAN_PROMPT="${CR_DIR}/prompts/spec-planning.md"
CREATED_DATE="$(date +%Y-%m-%d)"

# Prior CR baseline line (optional)
PRIOR_CR_LINE=""
if [[ -n "$PRIOR_CR" ]]; then
  PRIOR_CR_LINE="- **Prior CR baseline:** [development/cr${PRIOR_CR}/spec/spec.md](development/cr${PRIOR_CR}/spec/spec.md)"
fi

# Git tag: v0.N.0
GIT_TAG="v0.${CR_NUMBER}.0"

# ── Detect existing test infrastructure ──────────────────────────────────────
HAS_TEST_INFRA=false
[[ -f "docker-compose.test.yml" ]] && HAS_TEST_INFRA=true

# ── Test commands by type ─────────────────────────────────────────────────────
build_test_commands() {
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
    backend*)
      if [[ "$has_infra" == "true" ]]; then
        printf 'Run tests in two layers after every unit:\n\n**Layer 1 — Unit tests** (always, no Docker — fast feedback):\n- `bash scripts/test-unit.sh`\n\n**Layer 2 — API integration tests** (after any backend unit):\n- `bash scripts/test-api.sh`\n  Starts `test-db` + `backend-test` in Docker, runs `backend/tests/api/`, tears down with `down -v`.\n\nBoth layers must be green before marking a unit `[x]` complete.'
      else
        echo "- **Backend:** \`cd backend && python -m pytest -p no:cacheprovider --no-cov -q\`
- **Backend + coverage:** \`cd backend && python -m pytest --cov=app --cov-report=term-missing\`"
      fi ;;
    frontend*)
      echo "- **Frontend:** \`cd frontend && npx vitest run\`
- **TypeScript check:** \`cd frontend && npx tsc --noEmit\`" ;;
    *)
      echo "- (add test commands for your stack here)" ;;
  esac
}

# ── Tech constraints by type ──────────────────────────────────────────────────
build_tech_constraints() {
  local t="$1"
  case "$t" in
    backend*)
      echo "- Python 3.11+, FastAPI, SQLAlchemy, Pydantic, pytest + pytest-asyncio.
- No new dependencies without user confirmation.
- Alembic for all schema migrations — no raw SQL."
      ;;
    frontend*)
      echo "- TypeScript 5+, React 19, Vite, Tailwind CSS, Vitest + React Testing Library.
- No new dependencies without user confirmation.
- All colors via CSS variables / Tailwind utilities — no raw hex."
      ;;
    backend+frontend)
      echo "- Backend: Python 3.11+, FastAPI, SQLAlchemy, Pydantic, pytest.
- Frontend: TypeScript 5+, React 19, Vite, Tailwind CSS, Vitest.
- No new dependencies without user confirmation."
      ;;
    *)
      echo "- (list your tech stack constraints here)"
      ;;
  esac
}

TEST_COMMANDS="$(build_test_commands "$IMPL_TYPE" "$HAS_TEST_INFRA")"
TECH_CONSTRAINTS="$(build_tech_constraints "$IMPL_TYPE")"

CRITICAL_RULES="- After completing each unit, re-read the spec and plan before picking the next one.
- Do not mark a unit [x] until every test in its Test Plan section passes unmodified.
- If stuck on a single blocker for more than 3 tool-call rounds, mark the unit [!] Blocked with a clear reason and move on.
- Never skip, xfail, or comment out failing tests — fix the root cause or mark the unit blocked.
- Schema changes must be strictly additive unless the spec explicitly requires otherwise."

# ── Create directory tree ─────────────────────────────────────────────────────
echo "Creating directory tree: $CR_DIR"
mkdir -p "${CR_DIR}/spec" "${CR_DIR}/plan" "${CR_DIR}/progress" "${CR_DIR}/prompts"

CREATED=0

create_file() {
  local dst="$1" src="$2"
  shift 2
  if [[ -f "$dst" ]]; then
    echo "  SKIP  $dst (already exists)"
    return
  fi
  apply_template "$src" "$dst" "$@"
  echo "  CREATE $dst"
  CREATED=$((CREATED + 1))
}

# ── spec.md ───────────────────────────────────────────────────────────────────
if [[ -n "$SPEC_SRC" && -f "$SPEC_SRC" ]]; then
  if [[ ! -f "$SPEC_PATH" ]]; then
    cp "$SPEC_SRC" "$SPEC_PATH"
    echo "  CREATE $SPEC_PATH (copied from $SPEC_SRC)"
    CREATED=$((CREATED + 1))
  else
    echo "  SKIP  $SPEC_PATH (already exists)"
  fi
else
  create_file "$SPEC_PATH" "$RALPH_DIR/templates/spec.md" \
    "CR_NAME" "CR${CR_NUMBER}" \
    "CR_TITLE" "$CR_TITLE" \
    "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
    "CREATED_DATE" "$CREATED_DATE"
fi

# ── build.md ──────────────────────────────────────────────────────────────────
create_file "$BUILD_PROMPT" "$RALPH_DIR/templates/build.md" \
  "CR_NAME" "CR${CR_NUMBER}" \
  "CR_TITLE" "$CR_TITLE" \
  "SPEC_PATH" "$SPEC_PATH" \
  "PLAN_PATH" "$PLAN_PATH" \
  "PROGRESS_PATH" "$PROGRESS_PATH" \
  "PRIOR_CR_LINE" "$PRIOR_CR_LINE" \
  "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
  "CRITICAL_RULES" "$CRITICAL_RULES" \
  "TEST_COMMANDS" "$TEST_COMMANDS" \
  "TECH_CONSTRAINTS" "$TECH_CONSTRAINTS" \
  "GIT_TAG" "$GIT_TAG" \
  "SPECIAL_NOTES" "$SPECIAL_NOTES" \
  "CREATED_DATE" "$CREATED_DATE"

# ── spec-planning.md ──────────────────────────────────────────────────────────
create_file "$PLAN_PROMPT" "$RALPH_DIR/templates/spec-planning.md" \
  "SPEC_PATH" "$SPEC_PATH" \
  "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
  "PLAN_PATH" "$PLAN_PATH" \
  "EXISTING_CODE" "true" \
  "SPECIAL_NOTES" "$SPECIAL_NOTES"

# ── progress.md ───────────────────────────────────────────────────────────────
create_file "$PROGRESS_PATH" "$RALPH_DIR/templates/progress.md" \
  "CR_NAME" "CR${CR_NUMBER}" \
  "CR_TITLE" "$CR_TITLE"

# ── plan file frontmatter ─────────────────────────────────────────────────────
create_file "$PLAN_PATH" "$RALPH_DIR/templates/plan-header.md" \
  "CR_NAME" "CR${CR_NUMBER}" \
  "CR_TITLE" "$CR_TITLE" \
  "CR_NUMBER" "$CR_NUMBER" \
  "SPEC_PATH" "$SPEC_PATH" \
  "IMPLEMENTATION_TYPE" "$IMPL_TYPE" \
  "CREATED_DATE" "$CREATED_DATE"

# ── Verify no unresolved placeholders ────────────────────────────────────────
echo ""
echo "Verifying generated files..."
for f in "$BUILD_PROMPT" "$PLAN_PROMPT"; do
  verify_no_placeholders "$f"
done

# ── Offer to update loop_plan.sh ──────────────────────────────────────────────
if [[ -f "loop_plan.sh" ]]; then
  echo ""
  read -rp "Update loop_plan.sh to point to CR${CR_NUMBER} prompts? [Y/n]: " UPDATE_LOOP
  if [[ "${UPDATE_LOOP:-Y}" =~ ^[Yy]$ ]]; then
    sed -i "s|BUILD_PROMPT_FILE=.*|BUILD_PROMPT_FILE=\"${BUILD_PROMPT}\"|" loop_plan.sh
    sed -i "s|PLAN_PROMPT_FILE=.*|PLAN_PROMPT_FILE=\"${PLAN_PROMPT}\"|" loop_plan.sh
    echo "  UPDATED loop_plan.sh → CR${CR_NUMBER} prompts"
  fi
fi

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Done: $CREATED files created for CR${CR_NUMBER}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Next steps:"
echo "  1. Edit your spec:            $SPEC_PATH"
echo "  2. ralph status --cr ${CR_NUMBER}             (tells you what to do and what's missing)"
echo "  3. ralph start --mode plan  --cr ${CR_NUMBER}   (Claude generates implementation plan)"
echo "  4. ralph start --mode build --cr ${CR_NUMBER}   (Claude implements unit by unit)"
echo ""
echo "  Tip: run 'ralph status --cr ${CR_NUMBER}' at any point to see where you are."
echo ""
