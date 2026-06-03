#!/usr/bin/env bash
# ralph status — show CR progress and what to do next.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/tui.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
CR_NUMBER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cr) CR_NUMBER="$2"; shift 2 ;;
    *)    echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Resolve which CR to show ──────────────────────────────────────────────────
if [[ -z "$CR_NUMBER" ]]; then
  # Find the highest-numbered CR that has a progress file
  LATEST=""
  for d in development/cr*/; do
    n="${d#development/cr}"
    n="${n%/}"
    if [[ "$n" =~ ^[0-9]+$ && -f "${d}progress/implementation-progress.md" ]]; then
      if [[ -z "$LATEST" || "$n" -gt "$LATEST" ]]; then
        LATEST="$n"
      fi
    fi
  done
  # Also check development/initial/
  if [[ -z "$LATEST" && -f "development/initial/progress/implementation-progress.md" ]]; then
    LATEST="initial"
  fi
  CR_NUMBER="${LATEST:-}"
fi

# ── Resolve paths ─────────────────────────────────────────────────────────────
if [[ "$CR_NUMBER" == "initial" ]]; then
  CR_DIR="development/initial"
  CR_LABEL="Initial"
elif [[ -n "$CR_NUMBER" ]]; then
  CR_DIR="development/cr${CR_NUMBER}"
  CR_LABEL="CR${CR_NUMBER}"
else
  echo "No CR found. Run 'ralph add-cr' first."
  exit 0
fi

SPEC_FILE="${CR_DIR}/spec/spec.md"
PLAN_FILE="${CR_DIR}/plan/"*"-implementation-plan.md" 2>/dev/null || true
PROGRESS_FILE="${CR_DIR}/progress/implementation-progress.md"
BUILD_PROMPT="${CR_DIR}/prompts/build.md"
PLAN_PROMPT="${CR_DIR}/prompts/spec-planning.md"

tui_header "status — $CR_LABEL"

# ── Stage detection ───────────────────────────────────────────────────────────

STAGE="unknown"
STAGE_MSG=""
NEXT_CMD=""

spec_exists=false
spec_has_content=false
plan_exists=false
plan_has_units=false
progress_exists=false
any_complete=false
all_complete=false
has_blocked=false

[[ -f "$SPEC_FILE" ]] && spec_exists=true
if $spec_exists; then
  # Stub template has ~70 words. Treat spec as "real" when it exceeds 150 words.
  word_count=$(wc -w < "$SPEC_FILE" 2>/dev/null || echo "0")
  [[ "$word_count" -gt 150 ]] && spec_has_content=true
fi

# Resolve plan file (glob)
PLAN_PATH=""
for f in "${CR_DIR}/plan/"*"-implementation-plan.md"; do
  [[ -f "$f" ]] && PLAN_PATH="$f" && plan_exists=true && break
done

if $plan_exists && [[ -n "$PLAN_PATH" ]]; then
  unit_count=$(grep -c '^## Unit:' "$PLAN_PATH" 2>/dev/null || true)
  [[ "$unit_count" -ge 1 ]] && plan_has_units=true
fi

[[ -f "$PROGRESS_FILE" ]] && progress_exists=true

if $progress_exists; then
  complete_count=$(grep -c '^\- \[x\]' "$PROGRESS_FILE" 2>/dev/null || true)
  blocked_count=$(grep -c '^\- \[!\]' "$PROGRESS_FILE" 2>/dev/null || true)
  inprogress_count=$(grep -c '^\- \[~\]' "$PROGRESS_FILE" 2>/dev/null || true)
  notstarted_count=$(grep -c '^\- \[ \]' "$PROGRESS_FILE" 2>/dev/null || true)
  [[ "$complete_count" -gt 0 ]] && any_complete=true
  [[ "$blocked_count" -gt 0 ]] && has_blocked=true
  total_units=$((complete_count + blocked_count + inprogress_count + notstarted_count))
  [[ "$total_units" -gt 0 && "$notstarted_count" -eq 0 && "$inprogress_count" -eq 0 && "$blocked_count" -eq 0 ]] && all_complete=true
fi

# Determine current stage
if ! $spec_exists || ! $spec_has_content; then
  STAGE="1-write-spec"
elif ! $plan_exists || ! $plan_has_units; then
  STAGE="2-plan"
elif $any_complete || $inprogress_count -gt 0 2>/dev/null; then
  if $all_complete; then
    STAGE="5-done"
  elif $has_blocked; then
    STAGE="4-blocked"
  else
    STAGE="3-build"
  fi
elif $plan_has_units; then
  STAGE="3-ready-to-build"
else
  STAGE="2-plan"
fi

# ── Stage output ──────────────────────────────────────────────────────────────

print_file_status() {
  local label="$1" path="$2"
  if [[ -f "$path" ]]; then
    local lines
    lines=$(wc -l < "$path" 2>/dev/null || echo "?")
    tui_log_ok "$(printf '%-42s' "$label") ($lines lines)"
  else
    tui_log_err "$(printf '%-42s' "$label") (missing)"
  fi
}

printf 'Stage: '; tui_stage_badge "$STAGE"
echo ""
echo "Files:"
print_file_status "spec/spec.md" "$SPEC_FILE"
[[ -n "$PLAN_PATH" ]] && print_file_status "plan/$(basename "$PLAN_PATH")" "$PLAN_PATH" \
  || printf "  ✗ %-40s (missing)\n" "plan/*.md"
print_file_status "progress/implementation-progress.md" "$PROGRESS_FILE"
echo ""

case "$STAGE" in
  1-write-spec)
    echo "  You are here:  STEP 1 — Write the spec"
    echo ""
    echo "  The spec is the source of truth for everything that follows."
    echo "  Open it and fill in what you want to build:"
    echo ""
    echo "    ${SPEC_FILE}"
    echo ""
    echo "  Key sections to fill in:"
    echo "    §1  What the system does (2–3 paragraphs)"
    echo "    §3  Technology stack (be specific — versions matter)"
    echo "    §4  Functional requirements (numbered, testable)"
    echo "    §9  Acceptance criteria (how you'll know it's done)"
    echo ""
    NEXT_CMD="ralph start --mode plan --cr ${CR_NUMBER}"
    echo "  When the spec is ready, run:"
    echo "    $NEXT_CMD"
    ;;

  2-plan)
    echo "  You are here:  STEP 2 — Generate the implementation plan"
    echo ""
    echo "  Claude will read your spec and spawn 15+ subagents to:"
    echo "    • Extract all requirements (parallel, by spec section)"
    echo "    • Decompose them into atomic units (U001, U002, ...)"
    echo "    • Build dependency graph + test strategy"
    echo "    • Write the full plan to: $PLAN_PATH"
    echo ""
    echo "  Expected duration: 5–15 minutes"
    echo ""
    NEXT_CMD="ralph start --mode plan --cr ${CR_NUMBER}"
    echo "  Run:"
    echo "    $NEXT_CMD"
    ;;

  3-ready-to-build)
    echo "  You are here:  STEP 3 — Review plan, then build"
    echo ""
    echo "  The plan has been generated. Before starting the build loop:"
    echo ""
    echo "  REVIEW: ${PLAN_PATH}"
    echo ""
    echo "  Check that:"
    echo "    □ Each unit has a clear Goal (one sentence)"
    echo "    □ Dependencies are correct (no circular, no gaps)"
    echo "    □ Acceptance criteria are specific and testable"
    echo "    □ Test plan is realistic for your stack"
    echo "    □ Unit count feels right (1–3 days each)"
    echo ""
    echo "  Edit the plan directly if anything is off — Claude reads it fresh each iteration."
    echo ""
    NEXT_CMD="ralph start --mode build --cr ${CR_NUMBER}"
    echo "  When ready:"
    echo "    $NEXT_CMD"
    ;;

  3-build)
    echo "  You are here:  STEP 4 — Implementation in progress"
    echo ""
    if $progress_exists; then
      echo "  Unit progress:"
      printf "    %-12s %d\n" "Complete:"    "${complete_count:-0}"
      printf "    %-12s %d\n" "In progress:" "${inprogress_count:-0}"
      printf "    %-12s %d\n" "Not started:" "${notstarted_count:-0}"
      printf "    %-12s %d\n" "Blocked:"     "${blocked_count:-0}"
      if [[ "${total_units:-0}" -gt 0 ]]; then
        echo ""
        tui_progress "$complete_count" "$total_units"
      fi
    fi
    echo ""
    echo "  Each loop iteration:"
    echo "    1. Claude picks the highest-priority unblocked unit"
    echo "    2. Implements it fully (no stubs, no TODOs)"
    echo "    3. Runs the test suite — fixes all failures"
    echo "    4. Commits on green, marks unit complete in progress file"
    echo "    5. Loop repeats"
    echo ""
    echo "  Monitor: git log --oneline"
    echo "  Watch:   tail -f log/<latest>.log"
    echo ""
    NEXT_CMD="ralph start --mode build --cr ${CR_NUMBER}"
    echo "  Resume loop:"
    echo "    $NEXT_CMD"
    ;;

  4-blocked)
    echo "  You are here:  STEP 4 — Build loop has a BLOCKED unit"
    echo ""
    echo "  Check the progress file for blocked units ([!]):"
    echo "    ${PROGRESS_FILE}"
    echo ""
    echo "  Common causes:"
    echo "    • Missing spec — the unit references something not defined in spec.md"
    echo "    • Ambiguous requirement — multiple valid interpretations"
    echo "    • External dependency — waiting on an API, service, or another CR"
    echo ""
    echo "  Resolution:"
    echo "    1. Open the progress file and read the blocked unit's Notes"
    echo "    2. Update spec.md or the plan to clarify"
    echo "    3. Change status from [!] back to [ ] once resolved"
    echo ""
    NEXT_CMD="ralph start --mode build --cr ${CR_NUMBER}"
    echo "  Then resume:"
    echo "    $NEXT_CMD"
    ;;

  5-done)
    echo "  You are here:  STEP 5 — COMPLETE"
    echo ""
    echo "  All units complete. Final checklist:"
    echo ""
    echo "    □ All test suites pass (run them manually to confirm)"
    echo "    □ Progress file shows all [x] — no [!] or [ ] remaining"
    echo "    □ Git tag created (check: git tag --sort=-v:refname | head -1)"
    echo "    □ AGENTS.md updated with any new operational knowledge"
    echo ""
    echo "  Next:"
    echo "    • Create a PR from the current branch"
    echo "    • Start the next CR: ralph add-cr --number N+1"
    ;;
esac

echo ""
if tui_available; then
  gum style --foreground 240 "─────────────────────────────────────────"
else
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
fi

# ── Recent commits ────────────────────────────────────────────────────────────
if command -v git &>/dev/null && git rev-parse --is-inside-work-tree &>/dev/null 2>&1; then
  echo ""
  echo "Recent commits:"
  git log --oneline -6 2>/dev/null | sed 's/^/  /'
fi

echo ""
