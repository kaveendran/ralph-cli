#!/usr/bin/env bash
# Generic CR loop — accepts any prompt file as first argument.
# Usage: ./loop_cr.sh <prompt_file> [max_iterations]
# Examples:
#   ./loop_cr.sh development/cr5/prompts/build.md
#   ./loop_cr.sh development/cr5/prompts/build.md 20

if [ -z "$1" ]; then
    echo "Error: Prompt file is required"
    echo "Usage: ./loop_cr.sh <prompt_file> [max_iterations]"
    exit 1
fi

PROMPT_FILE="$1"
MAX_ITERATIONS=${2:-0}
ITERATION=0
CURRENT_BRANCH=$(git branch --show-current)
LOG_DIR="log"
mkdir -p "$LOG_DIR"
RUN_TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
PROMPT_NAME=$(basename "$PROMPT_FILE" .md)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Mode:   CR Implementation Loop"
echo "Prompt: $PROMPT_FILE"
echo "Branch: $CURRENT_BRANCH"
[ $MAX_ITERATIONS -gt 0 ] && echo "Max:    $MAX_ITERATIONS iterations" || echo "Max:    Unlimited"
echo "Logs:   $LOG_DIR/$RUN_TIMESTAMP-$PROMPT_NAME-*.log"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ ! -f "$PROMPT_FILE" ]; then
    echo "Error: Prompt file '$PROMPT_FILE' not found"
    exit 1
fi

while true; do
    if [ $MAX_ITERATIONS -gt 0 ] && [ $ITERATION -ge $MAX_ITERATIONS ]; then
        echo "Reached max iterations: $MAX_ITERATIONS"
        break
    fi

    echo -e "\n\n======================== LOOP $ITERATION ========================\n"

    LOG_FILE="$LOG_DIR/$RUN_TIMESTAMP-$PROMPT_NAME-iteration-$ITERATION.log"
    echo "Logging to: $LOG_FILE"

    cat "$PROMPT_FILE" | claude -p \
        --dangerously-skip-permissions \
        --output-format=stream-json \
        --model sonnet \
        --verbose 2>&1 | tee "$LOG_FILE"

    if ! git diff --cached --quiet || ! git diff --quiet; then
        git add -A
        git commit -m "loop(${PROMPT_NAME}): iteration ${ITERATION} — $(date +'%Y-%m-%d %H:%M:%S')"
    fi

    git push origin "$CURRENT_BRANCH" || git push -u origin "$CURRENT_BRANCH"

    ITERATION=$((ITERATION + 1))
done

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Loop completed: ${ITERATION} iterations"
echo "Logs saved in:  $LOG_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
