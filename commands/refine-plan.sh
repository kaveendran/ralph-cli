#!/usr/bin/env bash
# ralph refine-plan — bounded loop that pairs `ralph critique` with an
# apply-fixes subagent.
#
# Each iteration:
#   1. Run `ralph critique --cr N` (if no fresh report exists)
#   2. Spawn an apply-fixes subagent that proposes structural edits to the plan
#   3. Show the diff to the human
#   4. Ask: apply / skip-this-iteration / done / quit
#   5. If applied, loop back to step 1 (re-critique) up to --max iterations
#
# This is deliberately NOT fully autonomous — every plan edit is human-gated
# because LLM editing LLM-generated plans can drift if unbounded.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/tui.sh"

CR_NUMBER=""
MAX_ITERATIONS=3
GATE="human"    # human | auto (auto disables the prompt; CI use only)
SCOPE="critical,important"  # which severity levels to apply

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cr)    CR_NUMBER="$2";       shift 2 ;;
    --max)   MAX_ITERATIONS="$2";  shift 2 ;;
    --gate)  GATE="$2";            shift 2 ;;
    --scope) SCOPE="$2";           shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# Resolve latest CR with a plan if not given
if [[ -z "$CR_NUMBER" ]]; then
  LATEST=""
  for d in development/cr*/; do
    n="${d#development/cr}"; n="${n%/}"
    [[ "$n" =~ ^[0-9]+$ ]] || continue
    for f in "${d}plan/"*"-implementation-plan.md"; do
      [[ -f "$f" ]] || continue
      if [[ -z "$LATEST" || "$n" -gt "$LATEST" ]]; then LATEST="$n"; fi
    done
  done
  CR_NUMBER="${LATEST:-}"
fi

[[ -z "$CR_NUMBER" ]] && { echo "Error: no CR with a plan found. Use --cr N."; exit 1; }

CR_DIR="development/cr${CR_NUMBER}"
CR_NAME="CR${CR_NUMBER}"

PLAN_FILE=""
for f in "${CR_DIR}/plan/"*"-implementation-plan.md"; do
  [[ -f "$f" ]] && PLAN_FILE="$f" && break
done
[[ -z "$PLAN_FILE" ]] && { echo "Error: no plan in ${CR_DIR}/plan/."; exit 1; }

REVISIONS_DIR="${CR_DIR}/plan/revisions"
CRITIQUE_DIR="${CR_DIR}/critique"
mkdir -p "$REVISIONS_DIR" "$CRITIQUE_DIR"

# Save v0 snapshot once
V0="${REVISIONS_DIR}/v0-initial.md"
[[ ! -f "$V0" ]] && cp "$PLAN_FILE" "$V0"

tui_header "refine-plan — $CR_NAME" "plan: $(basename "$PLAN_FILE")  |  max: $MAX_ITERATIONS  |  gate: $GATE  |  scope: $SCOPE"

if ! command -v claude &>/dev/null; then
  echo "Error: 'claude' CLI not found in PATH."; exit 1
fi

# ── Helper: get the latest critique report file (newest mtime) ────────────────
latest_critique_report() {
  ls -t "$CRITIQUE_DIR"/*-report.md 2>/dev/null | head -1 || true
}

# ── Helper: count CRITICAL / IMPORTANT items in a report ──────────────────────
count_severity() {
  local report="$1" sev="$2"
  # Severity sections look like:  "### CRITICAL — Fix before starting build"
  awk -v sev="$sev" '
    BEGIN { in_section=0; count=0 }
    /^### / {
      if ($0 ~ sev) { in_section=1; next }
      else if (in_section) { in_section=0 }
    }
    in_section && /^[0-9]+\./ { count++ }
    END { print count }
  ' "$report"
}

ITER=0
CONSECUTIVE_NOOPS=0

while [[ $ITER -lt $MAX_ITERATIONS ]]; do
  ITER=$((ITER + 1))
  echo ""
  if tui_available; then
    gum style --foreground 99 --bold "  ── refine iteration $ITER / $MAX_ITERATIONS ──"
  else
    echo "──── refine iteration $ITER / $MAX_ITERATIONS ────"
  fi

  # ── Step 1: critique (always re-run on iter > 1 since plan changed) ────────
  if [[ $ITER -eq 1 ]] && [[ -n "$(latest_critique_report)" ]]; then
    REPORT="$(latest_critique_report)"
    tui_log_ok "Using existing critique report: $REPORT"
  else
    tui_section "Running critique"
    bash "$RALPH_DIR/commands/critique.sh" --cr "$CR_NUMBER" > /dev/null
    REPORT="$(latest_critique_report)"
    [[ -z "$REPORT" ]] && { echo "Error: critique produced no report."; exit 1; }
  fi

  CRIT_COUNT=$(count_severity "$REPORT" "CRITICAL")
  IMPT_COUNT=$(count_severity "$REPORT" "IMPORTANT")
  echo ""
  printf '  Severity counts: \033[31mCRITICAL=%s\033[0m  \033[33mIMPORTANT=%s\033[0m\n' \
    "$CRIT_COUNT" "$IMPT_COUNT"

  # Convergence: 0 critical AND 0 important → done
  if [[ "${CRIT_COUNT:-0}" -eq 0 && "${IMPT_COUNT:-0}" -eq 0 ]]; then
    tui_log_ok "Plan has converged — no CRITICAL or IMPORTANT items remain."
    break
  fi

  # ── Step 2: apply-fixes subagent (proposes edits to a candidate file) ──────
  tui_section "Generating proposed plan edits"

  CANDIDATE="${REVISIONS_DIR}/v${ITER}-candidate.md"
  PLAN_BEFORE=$(cat "$PLAN_FILE")
  REPORT_CONTENT=$(cat "$REPORT")

  printf 'You are revising an implementation plan based on a critique report.\n\nRules:\n- Apply only items in scope: %s.\n- Make STRUCTURAL edits (missing sections, missing rollback, broken deps, vague-but-fixable ACs, missing edge cases in test plans, missing units).\n- Do NOT invent new technical details that are not derivable from the existing plan + critique. If a critique item requires information the plan does not contain, leave the plan unchanged for that item and add an entry to the "Open Questions" section instead.\n- Preserve unit IDs. New units use the next available UNN. Update the Implementation Order and Parallel Bundles sections to include new units.\n- Output the COMPLETE revised plan. No preamble, no diff, no explanation — just the full markdown file.\n- Do not change unit names or remove units, even if the critique suggests removal. If a unit should be removed, mark it deprecated in its header instead and keep the body for audit.\n\n---\nCRITIQUE REPORT:\n%s\n\n---\nCURRENT PLAN:\n%s\n' \
    "$SCOPE" "$REPORT_CONTENT" "$PLAN_BEFORE" \
    | claude -p --dangerously-skip-permissions --output-format text --model sonnet \
      > "$CANDIDATE" 2>/dev/null

  if [[ ! -s "$CANDIDATE" ]]; then
    tui_log_warn "apply-fixes produced no output — stopping."
    break
  fi

  # ── Detect no-op (LLM returned same content) ───────────────────────────────
  if cmp -s "$PLAN_FILE" "$CANDIDATE"; then
    CONSECUTIVE_NOOPS=$((CONSECUTIVE_NOOPS + 1))
    tui_log_warn "apply-fixes returned no changes (no-op iteration)."
    if [[ $CONSECUTIVE_NOOPS -ge 1 ]]; then
      tui_log_ok "Stopping — apply-fixes is no longer changing the plan."
      break
    fi
    continue
  else
    CONSECUTIVE_NOOPS=0
  fi

  # ── Show diff summary ──────────────────────────────────────────────────────
  echo ""
  tui_section "Proposed changes"
  if command -v diff &>/dev/null; then
    diff -u "$PLAN_FILE" "$CANDIDATE" | head -120 || true
    echo ""
    echo "  ... (truncated to first 120 diff lines — full candidate: $CANDIDATE)"
  else
    echo "  diff not available; candidate written to $CANDIDATE"
  fi

  # ── Step 3+4: human gate ───────────────────────────────────────────────────
  ACTION="apply"
  if [[ "$GATE" == "human" ]]; then
    echo ""
    ACTION="$(tui_choose "Apply this revision?" \
      "apply  — accept these changes and re-critique" \
      "skip   — reject this revision, continue to next iteration" \
      "done   — accept and stop the loop here" \
      "quit   — reject and stop the loop here")"
    ACTION="${ACTION%% *}"
  fi

  case "$ACTION" in
    apply|done)
      cp "$CANDIDATE" "${REVISIONS_DIR}/v${ITER}.md"
      cp "$CANDIDATE" "$PLAN_FILE"
      tui_log_ok "Applied revision v${ITER} to $PLAN_FILE"
      [[ "$ACTION" == "done" ]] && break
      ;;
    skip)
      tui_log_skip "Skipped revision v${ITER} (candidate kept at $CANDIDATE for review)"
      ;;
    quit)
      tui_log_skip "Stopped by user. Candidate kept at $CANDIDATE."
      break
      ;;
  esac
done

echo ""
tui_header "refine-plan complete" "$ITER iteration(s)  |  revisions: $REVISIONS_DIR"
echo "Next:"
echo "  - Re-read the plan: $PLAN_FILE"
echo "  - When you're satisfied: ralph start --mode build --cr $CR_NUMBER"
echo ""
