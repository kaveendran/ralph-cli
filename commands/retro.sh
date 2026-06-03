#!/usr/bin/env bash
# ralph retro — post-build retrospective.
# Runs 3 parallel Sonnet agents:
#   1. plan-vs-reality — diff intended plan vs what actually shipped
#   2. blocker-patterns — recurring blockers across this and prior CRs
#   3. test-coverage   — units whose ACs lack matching tests
# Then a synthesizer writes: retro report, draft RELEASE_NOTES.md, draft PR description.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/tui.sh"

CR_NUMBER=""
WRITE_FILES=1   # also produce RELEASE_NOTES.md and PR_DESCRIPTION.md
while [[ $# -gt 0 ]]; do
  case "$1" in
    --cr)         CR_NUMBER="$2";   shift 2 ;;
    --no-files)   WRITE_FILES=0;    shift   ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Resolve latest CR with a progress file if not specified
if [[ -z "$CR_NUMBER" ]]; then
  LATEST=""
  for d in development/cr*/; do
    n="${d#development/cr}"
    n="${n%/}"
    [[ "$n" =~ ^[0-9]+$ && -f "${d}progress/implementation-progress.md" ]] || continue
    if [[ -z "$LATEST" || "$n" -gt "$LATEST" ]]; then LATEST="$n"; fi
  done
  CR_NUMBER="${LATEST:-}"
fi

if [[ -z "$CR_NUMBER" ]]; then
  echo "Error: no CR with a progress file found. Use --cr N."
  exit 1
fi

CR_DIR="development/cr${CR_NUMBER}"
CR_NAME="CR${CR_NUMBER}"
SPEC_FILE="${CR_DIR}/spec/spec.md"
PROGRESS_FILE="${CR_DIR}/progress/implementation-progress.md"

PLAN_FILE=""
for f in "${CR_DIR}/plan/"*"-implementation-plan.md"; do
  [[ -f "$f" ]] && PLAN_FILE="$f" && break
done

if [[ ! -f "$PROGRESS_FILE" || -z "$PLAN_FILE" ]]; then
  echo "Error: missing plan or progress file for ${CR_NAME}."
  exit 1
fi

tui_header "retro — $CR_NAME" "plan: $(basename "$PLAN_FILE")"

# Gather prior-CR progress files for the blocker-patterns agent
PRIOR_PROGRESS=""
for d in development/cr*/; do
  n="${d#development/cr}"; n="${n%/}"
  [[ "$n" =~ ^[0-9]+$ ]] || continue
  [[ "$n" == "$CR_NUMBER" ]] && continue
  pf="${d}progress/implementation-progress.md"
  [[ -f "$pf" ]] && PRIOR_PROGRESS+="--- cr${n}/progress ---"$'\n'"$(cat "$pf")"$'\n\n'
done
[[ -z "$PRIOR_PROGRESS" ]] && PRIOR_PROGRESS="(no prior CRs)"

# Git log for the CR (last 200 commits — well above any realistic CR)
GIT_LOG=$(git log --oneline -200 2>/dev/null || echo "(no git history)")

PLAN_CONTENT=$(cat "$PLAN_FILE")
PROGRESS_CONTENT=$(cat "$PROGRESS_FILE")
SPEC_CONTENT=""
[[ -f "$SPEC_FILE" ]] && SPEC_CONTENT=$(cat "$SPEC_FILE")

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

PVR_OUT="$TMP_DIR/plan-vs-reality.txt"
BP_OUT="$TMP_DIR/blocker-patterns.txt"
TC_OUT="$TMP_DIR/test-coverage.txt"

tui_section "Running 3 retrospective agents in parallel"

# ── Agent 1: plan-vs-reality ──────────────────────────────────────────────────
printf 'You are a senior engineer running a post-build retrospective.\n\nCompare the INTENDED plan against what actually shipped. Use the plan, the progress file, and the git log to produce a diff.\n\nReview:\n1. Units added during build that were not in the original plan (scope creep).\n2. Units in the original plan that were skipped, merged, or dropped.\n3. Units that were re-ordered relative to the planned Implementation Order.\n4. Units that took materially longer than expected (look at git timestamps for unit commits).\n5. Acceptance criteria that changed between plan and shipped code (inferred from progress notes).\n\nOutput:\n## Plan-vs-Reality\n\n### Scope additions\n(list with reason if visible in progress notes)\n\n### Scope reductions\n(list with reason)\n\n### Re-orderings\n(list)\n\n### Notable AC changes\n(list)\n\n### Summary (3 bullets max)\n-\n\n---\nPLAN:\n%s\n\n---\nPROGRESS:\n%s\n\n---\nGIT LOG:\n%s\n' \
  "$PLAN_CONTENT" "$PROGRESS_CONTENT" "$GIT_LOG" \
  | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
    > "$PVR_OUT" 2>/dev/null &
PVR_PID=$!

# ── Agent 2: blocker patterns ─────────────────────────────────────────────────
printf 'You are a senior engineer analyzing blocker patterns across CRs.\n\nRead the current progress file plus every prior CR progress file. Look for blockers (lines marked [!] Blocked or notes mentioning "blocked", "stuck", "waiting on") and surface RECURRING patterns worth adding to a lessons-learned doc.\n\nReview:\n1. Categories of blockers that show up more than once across CRs (e.g., "missing spec for auth flow", "test fixtures contention", "schema migration timing").\n2. Anti-patterns visible in how blockers were resolved (e.g., skipping tests, scope expansion to fix a blocker).\n3. Process gaps the blockers point to (spec quality, plan granularity, test infra).\n4. Concrete additions to development/shared/lessons.md — one short rule per recurring pattern.\n\nOutput:\n## Blocker Patterns\n\n### Recurring blocker categories\n(numbered, with citations like "cr3:U007, cr5:U012")\n\n### Process gaps\n(numbered)\n\n### Suggested lessons.md entries\n(markdown bullet list ready to paste)\n\n---\nCURRENT CR PROGRESS:\n%s\n\n---\nPRIOR CR PROGRESS FILES:\n%s\n' \
  "$PROGRESS_CONTENT" "$PRIOR_PROGRESS" \
  | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
    > "$BP_OUT" 2>/dev/null &
BP_PID=$!

# ── Agent 3: test-coverage gaps ───────────────────────────────────────────────
printf 'You are a senior test engineer auditing whether the SHIPPED implementation has tests matching each acceptance criterion.\n\nFor each unit marked [x] complete in the progress file, check the plan'\''s Acceptance Criteria for that unit and verify (by reading the spec'\''s ACs and the unit'\''s declared Test Plan) that each AC has a matching test in the codebase.\n\nYou do NOT need to actually read test files — base findings on the plan'\''s Test Plan sections and on whether ACs are testable as written. Flag:\n1. ACs that are vague ("works correctly") and almost certainly have no specific test.\n2. ACs that the unit Test Plan does not visibly cover.\n3. Units marked [x] whose Test Plan is empty or trivial.\n4. Spec-level ACs (top-level numbered ACs) that no unit'\''s test plan visibly covers.\n\nOutput:\n## Test Coverage Gaps\n\n### Likely-untested ACs (per unit)\n(citing UNIT_ID + AC text)\n\n### Spec-level ACs without unit coverage\n(citing AC-NNN)\n\n### Units shipped without substantive tests\n(citing UNIT_ID)\n\n### Top 3 coverage gaps to address before tagging release\n1.\n2.\n3.\n\n---\nSPEC:\n%s\n\n---\nPLAN:\n%s\n\n---\nPROGRESS:\n%s\n' \
  "$SPEC_CONTENT" "$PLAN_CONTENT" "$PROGRESS_CONTENT" \
  | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
    > "$TC_OUT" 2>/dev/null &
TC_PID=$!

tui_wait_pids "Retrospective agents running..." "$PVR_PID" "$BP_PID" "$TC_PID"
wait "$PVR_PID" "$BP_PID" "$TC_PID" 2>/dev/null || true

tui_section "Synthesizing retrospective + draft release notes"

PVR_REVIEW=$(cat "$PVR_OUT")
BP_REVIEW=$(cat "$BP_OUT")
TC_REVIEW=$(cat "$TC_OUT")

RETRO_DIR="${CR_DIR}/retro"
mkdir -p "$RETRO_DIR"
RETRO_REPORT="${RETRO_DIR}/$(date +%Y%m%d-%H%M%S)-retro.md"

# Synthesizer also produces structured sections we can split for RELEASE_NOTES + PR
printf 'You are a senior engineering lead writing the retrospective for %s.\n\nProduce ONE report combining the three retrospective agents below. The report MUST have exactly these top-level sections, in this order, using these exact headings (the heading text is parsed by tooling):\n\n# %s Retrospective\n\n## Summary\n(3–5 sentence executive summary: what shipped, headline risks, headline wins.)\n\n## Plan vs Reality\n(merge agent 1 findings, deduplicated)\n\n## Blocker Patterns\n(merge agent 2 findings)\n\n## Test Coverage Gaps\n(merge agent 3 findings)\n\n## Action Items Before Release\n(numbered, with severity tag MUST-FIX / SHOULD-FIX / NICE-TO-HAVE)\n\n## RELEASE_NOTES draft\n(markdown ready to paste into RELEASE_NOTES.md — sections: ### Features, ### Changes, ### Migrations, ### Known Issues. Pull from completed units.)\n\n## PR Description draft\n(markdown ready to paste into a PR body — sections: ## Summary, ## Changes, ## Testing, ## Risk & Rollback. Reference unit IDs.)\n\n---\nAGENT 1 — PLAN-VS-REALITY:\n%s\n\n---\nAGENT 2 — BLOCKER PATTERNS:\n%s\n\n---\nAGENT 3 — TEST COVERAGE:\n%s\n' \
  "$CR_NAME" "$CR_NAME" \
  "$PVR_REVIEW" "$BP_REVIEW" "$TC_REVIEW" \
  | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
  | tee "$RETRO_REPORT"

echo ""
tui_log_ok "Retro report saved: $RETRO_REPORT"

# ── Split out RELEASE_NOTES and PR description into stand-alone files ─────────
if [[ $WRITE_FILES -eq 1 ]]; then
  RELEASE_NOTES="${RETRO_DIR}/RELEASE_NOTES.md"
  PR_DESC="${RETRO_DIR}/PR_DESCRIPTION.md"

  awk '
    /^## RELEASE_NOTES draft/        { mode="rn"; next }
    /^## PR Description draft/       { mode="pr"; next }
    /^## /                           { mode="" }
    mode=="rn"                       { print > RNFILE }
    mode=="pr"                       { print > PRFILE }
  ' RNFILE="$RELEASE_NOTES" PRFILE="$PR_DESC" "$RETRO_REPORT"

  [[ -s "$RELEASE_NOTES" ]] && tui_log_ok "Draft release notes: $RELEASE_NOTES"
  [[ -s "$PR_DESC"       ]] && tui_log_ok "Draft PR description: $PR_DESC"
fi

echo ""
echo "Next:"
echo "  1. Address any MUST-FIX action items."
echo "  2. Copy ${RETRO_DIR}/RELEASE_NOTES.md into the project's RELEASE_NOTES.md."
echo "  3. Use ${RETRO_DIR}/PR_DESCRIPTION.md as the PR body when shipping."
echo ""
