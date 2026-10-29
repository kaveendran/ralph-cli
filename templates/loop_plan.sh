#!/usr/bin/env bash
# Ralph Loop — drives Claude Code headlessly on a repeating build or plan cycle.
# Usage: ./loop_plan.sh [plan] [max_iterations]
# Examples:
#   ./loop_plan.sh              # Build mode, unlimited iterations
#   ./loop_plan.sh 20           # Build mode, max 20 iterations
#   ./loop_plan.sh plan         # Plan mode, unlimited iterations
#   ./loop_plan.sh plan 5       # Plan mode, max 5 iterations

# ── Configuration ─────────────────────────────────────────────────────────────
# Edit these paths when starting a new CR.
BUILD_PROMPT_FILE="development/CURRENT_CR/prompts/build.md"
PLAN_PROMPT_FILE="development/CURRENT_CR/prompts/spec-planning.md"

# Model selection: "opus" for complex reasoning, "sonnet" for speed.
BUILD_MODEL="sonnet"
PLAN_MODEL="opus"
# ──────────────────────────────────────────────────────────────────────────────

if [ "$1" = "plan" ]; then
    MODE="plan"
    PROMPT_FILE="$PLAN_PROMPT_FILE"
    MODEL="$PLAN_MODEL"
    MAX_ITERATIONS=${2:-0}
elif [[ "$1" =~ ^[0-9]+$ ]]; then
    MODE="build"
    PROMPT_FILE="$BUILD_PROMPT_FILE"
    MODEL="$BUILD_MODEL"
    MAX_ITERATIONS=$1
else
    MODE="build"
    PROMPT_FILE="$BUILD_PROMPT_FILE"
    MODEL="$BUILD_MODEL"
    MAX_ITERATIONS=0
fi

ITERATION=0
CURRENT_BRANCH=$(git branch --show-current)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Mode:   $MODE"
echo "Prompt: $PROMPT_FILE"
echo "Model:  $MODEL"
echo "Branch: $CURRENT_BRANCH"
[ $MAX_ITERATIONS -gt 0 ] && echo "Max:    $MAX_ITERATIONS iterations" || echo "Max:    Unlimited"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ! -f "$PROMPT_FILE" ]; then
    echo "Error: $PROMPT_FILE not found"
    exit 1
fi

while true; do
    if [ $MAX_ITERATIONS -gt 0 ] && [ $ITERATION -ge $MAX_ITERATIONS ]; then
        echo "Reached max iterations: $MAX_ITERATIONS"
        break
    fi

    echo -e "\n\n======================== LOOP $ITERATION ========================\n"

    cat "$PROMPT_FILE" | claude -p \
        --dangerously-skip-permissions \
        --output-format=stream-json \
        --model "$MODEL" \
        --verbose

    git push origin "$CURRENT_BRANCH" || {
        echo "Failed to push. Creating remote branch..."
        git push -u origin "$CURRENT_BRANCH"
    }

    ITERATION=$((ITERATION + 1))
done
