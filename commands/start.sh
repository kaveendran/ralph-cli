#!/usr/bin/env bash
# ralph start — run the Ralph Loop for plan or build mode.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/context.sh"
source "$RALPH_DIR/lib/tui.sh"
source "$RALPH_DIR/lib/index.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
MODE="build"
CR_NUMBER=""
PROMPT_FILE=""
MAX_ITERATIONS=0
MODEL=""
MAX_STALLS=3       # consecutive stalled iterations before auto-stop (0 = disabled)
STOP_ON_REGRESS=1  # auto-stop if [x] count decreases

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)            MODE="$2";          shift 2 ;;
    --cr)              CR_NUMBER="$2";     shift 2 ;;
    --prompt)          PROMPT_FILE="$2";   shift 2 ;;
    --max)             MAX_ITERATIONS="$2"; shift 2 ;;
    --model)           MODEL="$2";         shift 2 ;;
    --max-stalls)      MAX_STALLS="$2";    shift 2 ;;
    --no-stop-regress) STOP_ON_REGRESS=0;  shift   ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── Resolve prompt file ───────────────────────────────────────────────────────
if [[ -z "$PROMPT_FILE" ]]; then
  if [[ -z "$CR_NUMBER" ]]; then
    # Try to detect from loop_plan.sh or fall back to initial/
    if [[ -f "loop_plan.sh" ]]; then
      if [[ "$MODE" == "plan" ]]; then
        PROMPT_FILE=$(grep 'PLAN_PROMPT_FILE=' loop_plan.sh | head -1 | sed 's/.*="\(.*\)"/\1/')
      else
        PROMPT_FILE=$(grep 'BUILD_PROMPT_FILE=' loop_plan.sh | head -1 | sed 's/.*="\(.*\)"/\1/')
      fi
    fi
    if [[ -z "$PROMPT_FILE" ]]; then
      if [[ "$MODE" == "plan" ]]; then
        PROMPT_FILE="development/initial/prompts/spec-planning.md"
      else
        PROMPT_FILE="development/initial/prompts/build.md"
      fi
    fi
  else
    if [[ "$MODE" == "plan" ]]; then
      PROMPT_FILE="development/cr${CR_NUMBER}/prompts/spec-planning.md"
    else
      PROMPT_FILE="development/cr${CR_NUMBER}/prompts/build.md"
    fi
  fi
fi

# Default model: opus for plan, sonnet for build
if [[ -z "$MODEL" ]]; then
  [[ "$MODE" == "plan" ]] && MODEL="opus" || MODEL="sonnet"
fi

# ── Validate ──────────────────────────────────────────────────────────────────
if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "Error: Prompt file not found: $PROMPT_FILE"
  echo ""
  echo "Suggestions:"
  if [[ -n "$CR_NUMBER" ]]; then
    echo "  Run 'ralph add-cr --number $CR_NUMBER' first to scaffold the CR."
  else
    echo "  Run 'ralph init' first, or specify --cr N or --prompt <path>."
  fi
  exit 1
fi

CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
ITERATION=0
LOG_DIR="log"
mkdir -p "$LOG_DIR"
RUN_TIMESTAMP=$(date +"%Y%m%d-%H%M%S")

# Resolve progress file (build: context injection + auto-stop; plan: auto-populate after loop)
PROGRESS_FILE=$(infer_progress_file "$PROMPT_FILE")

_max_str="unlimited"; [[ $MAX_ITERATIONS -gt 0 ]] && _max_str="max $MAX_ITERATIONS"
tui_header "start — $MODE mode" "model: $MODEL  |  branch: $CURRENT_BRANCH  |  iterations: $_max_str"

TMP_PROMPT=$(mktemp)
trap 'rm -f "$TMP_PROMPT"' EXIT

# ── Loop ──────────────────────────────────────────────────────────────────────
while true; do
  if [[ $MAX_ITERATIONS -gt 0 && $ITERATION -ge $MAX_ITERATIONS ]]; then
    echo "Reached max iterations: $MAX_ITERATIONS"
    break
  fi

  # Auto-stop when all units are done (build mode only)
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" ]]; then
    if all_units_done "$PROGRESS_FILE"; then
      tui_header "All units complete" "run 'ralph status' to verify and see next steps"
      break
    fi
  fi

  echo ""
  if tui_available; then
    gum style --foreground 99 --bold "  ── Iteration $ITERATION ──"
  else
    echo "======================== LOOP $ITERATION ========================"
  fi

  # Show progress bar at the start of each build iteration
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" && -f "$PROGRESS_FILE" ]]; then
    _c=$(grep  -c '^\- \[x\]'  "$PROGRESS_FILE" 2>/dev/null || true)
    _ip=$(grep -c '^\- \[~\]'  "$PROGRESS_FILE" 2>/dev/null || true)
    _ns=$(grep -c '^\- \[ \]'  "$PROGRESS_FILE" 2>/dev/null || true)
    _bl=$(grep -c '^\- \[!\]'  "$PROGRESS_FILE" 2>/dev/null || true)
    _t=$((_c + _ip + _ns + _bl))
    [[ "${_t:-0}" -gt 0 ]] && tui_progress "${_c:-0}" "$_t"
  fi
  echo ""

  LOG_FILE="$LOG_DIR/${RUN_TIMESTAMP}-$(basename "$PROMPT_FILE" .md)-iteration-${ITERATION}.log"

  # Build prompt: prepend compact loop state in build mode
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" ]]; then
    generate_context "$PROGRESS_FILE" > "$TMP_PROMPT"
    cat "$PROMPT_FILE" >> "$TMP_PROMPT"
    INPUT_FILE="$TMP_PROMPT"
  else
    INPUT_FILE="$PROMPT_FILE"
  fi

  # Snapshot pre-iteration state for the index (never let a snapshot failure
  # take down the whole loop — index recording is best-effort).
  ITER_BEFORE=""
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" ]]; then
    ITER_BEFORE=$(index_snapshot_before "$PROGRESS_FILE" 2>/dev/null || echo "")
  fi

  # Visible "we got to the claude call" marker — Opus + stream-json can take
  # 10–30s to emit its first line, so without this the screen looks frozen.
  tui_log_ok "Invoking claude (model=$MODEL, prompt=$(basename "$PROMPT_FILE"), log=$LOG_FILE)"
  echo "  (first output line typically appears in 10–30s for Opus, 5–10s for Sonnet)"
  echo ""

  cat "$INPUT_FILE" | claude -p \
    --dangerously-skip-permissions \
    --output-format=stream-json \
    --model "$MODEL" \
    --verbose 2>&1 | tee "$LOG_FILE"

  # Push — create remote branch if it doesn't exist yet
  git push origin "$CURRENT_BRANCH" 2>/dev/null || {
    echo "Creating remote tracking branch..."
    git push -u origin "$CURRENT_BRANCH"
  }

  # Record structured iteration index entry (build mode only)
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" && -n "$ITER_BEFORE" ]]; then
    index_record_after "$ITERATION" "$MODE" "${CR_NUMBER:-initial}" "$MODEL" \
      "$PROMPT_FILE" "$LOG_FILE" "$PROGRESS_FILE" "$ITER_BEFORE"

    # #24 Test-ran verification: warn if expected layered runners not in log
    if grep -qF 'scripts/test-' "$PROMPT_FILE" 2>/dev/null; then
      MISSING=""
      for cmd in scripts/test-unit.sh scripts/test-api.sh scripts/test-e2e.sh; do
        if grep -qF "$cmd" "$PROMPT_FILE" && ! grep -qF "$cmd" "$LOG_FILE"; then
          MISSING+="$cmd "
        fi
      done
      [[ -n "$MISSING" ]] && tui_log_warn "Iteration $ITERATION did not run expected test command(s): $MISSING"
    fi

    # Stop on regression
    if [[ $STOP_ON_REGRESS -eq 1 ]] && tail -n 1 "$LOG_DIR/index.jsonl" 2>/dev/null | grep -q '"regressed":true'; then
      tui_log_warn "Regression detected — completed unit count went down. Stopping loop."
      ITERATION=$((ITERATION + 1))
      break
    fi

    # Stop on consecutive stalls
    if [[ $MAX_STALLS -gt 0 ]]; then
      STALL_COUNT=$(index_recent_stalls "$MAX_STALLS")
      if [[ ${STALL_COUNT:-0} -ge $MAX_STALLS ]]; then
        tui_log_warn "$MAX_STALLS consecutive iterations with no progress — stopping. Re-run with --max-stalls 0 to disable."
        ITERATION=$((ITERATION + 1))
        break
      fi
    fi
  fi

  ITERATION=$((ITERATION + 1))
done

# After plan mode — auto-populate progress file from the generated plan units
if [[ "$MODE" == "plan" ]]; then
  _plan_file=$(infer_plan_file "$PROMPT_FILE")
  if [[ -n "$_plan_file" ]]; then
    populate_progress_from_plan "$_plan_file" "$PROGRESS_FILE" \
      && tui_log_ok "Progress file populated from plan: $PROGRESS_FILE"
  fi
fi

tui_header "Loop complete" "$ITERATION iteration(s)  |  logs in: $LOG_DIR"
