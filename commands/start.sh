#!/usr/bin/env bash
# ralph start — run the Ralph Loop for plan or build mode.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/context.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
MODE="build"
CR_NUMBER=""
PROMPT_FILE=""
MAX_ITERATIONS=0
MODEL=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)   MODE="$2";          shift 2 ;;
    --cr)     CR_NUMBER="$2";     shift 2 ;;
    --prompt) PROMPT_FILE="$2";   shift 2 ;;
    --max)    MAX_ITERATIONS="$2"; shift 2 ;;
    --model)  MODEL="$2";         shift 2 ;;
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

# Resolve progress file for build mode (used for context injection + auto-stop)
PROGRESS_FILE=""
if [[ "$MODE" == "build" ]]; then
  PROGRESS_FILE=$(infer_progress_file "$PROMPT_FILE")
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ralph start"
echo "  Mode:   $MODE"
echo "  Prompt: $PROMPT_FILE"
echo "  Model:  $MODEL"
echo "  Branch: $CURRENT_BRANCH"
[[ $MAX_ITERATIONS -gt 0 ]] && echo "  Max:    $MAX_ITERATIONS iterations" || echo "  Max:    Unlimited"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

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
      echo ""
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      echo "  All units complete — loop exiting."
      echo "  Run 'ralph status' to verify and see next steps."
      echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
      break
    fi
  fi

  echo ""
  echo "======================== LOOP $ITERATION ========================"
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

  ITERATION=$((ITERATION + 1))
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Loop complete: $ITERATION iteration(s)"
echo "  Logs in: $LOG_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
