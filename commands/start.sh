#!/usr/bin/env bash
# ralph start — run the Ralph Loop for plan or build mode.
#
# Each iteration is one bounded, fresh `claude -p` session (see P-1 in
# docs/meta-harness-plan.md):
#   - auto-compaction at 200k tokens instead of the ~967K default on 1M models
#   - project-only MCP servers and settings, no auto memory
#   - turn and budget caps per iteration
#   - build mode: one unit per iteration, with only that unit's plan entry,
#     and an `explorer` subagent for codebase searches
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/context.sh"
source "$RALPH_DIR/lib/tui.sh"
source "$RALPH_DIR/lib/index.sh"

# ── Arg parsing ───────────────────────────────────────────────────────────────
MODE="build"
CR_NUMBER=""
PROMPT_FILE=""
MAX_ITERATIONS=""  # default: 1 for plan mode, 0 (unlimited) for build mode
MODEL=""
MAX_STALLS=3       # consecutive stalled iterations before auto-stop (0 = disabled)
STOP_ON_REGRESS=1  # auto-stop if [x] count decreases

# Per-iteration bounds. Flags win over environment variables; unset ones get
# per-mode defaults after parsing (plan mode is one long Opus session).
MAX_TURNS="${RALPH_MAX_TURNS:-}"
MAX_BUDGET="${RALPH_MAX_BUDGET:-}"
COMPACT_WINDOW="${RALPH_COMPACT_WINDOW:-200000}"
SETTING_SOURCES="${RALPH_SETTING_SOURCES:-project,local}"
HERMETIC=1          # project-only MCP servers and settings, no auto memory
PUSH=0              # sync is manual: push only when --push is given
DRY_RUN=0
MAX_AGENT_FAILURES=3                       # consecutive failed claude runs before stopping
RETRY_DELAY="${RALPH_RETRY_DELAY:-60}"     # seconds; doubles after each failure

usage_error() { echo "Error: $1" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || usage_error "$1 needs a value."; }
need_int()   { [[ "$2" =~ ^[0-9]+$ ]] || usage_error "$1 must be a whole number (got '$2')."; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)            need_value "$@"; MODE="$2";            shift 2 ;;
    --cr)              need_value "$@"; CR_NUMBER="$2";       shift 2 ;;
    --prompt)          need_value "$@"; PROMPT_FILE="$2";     shift 2 ;;
    --max)             need_value "$@"; need_int "$1" "$2"; MAX_ITERATIONS="$2"; shift 2 ;;
    --model)           need_value "$@"; MODEL="$2";           shift 2 ;;
    --max-stalls)      need_value "$@"; need_int "$1" "$2"; MAX_STALLS="$2"; shift 2 ;;
    --max-turns)       need_value "$@"; need_int "$1" "$2"; MAX_TURNS="$2"; shift 2 ;;
    --max-budget)      need_value "$@"; MAX_BUDGET="$2";      shift 2 ;;
    --compact-window)  need_value "$@"; need_int "$1" "$2"; COMPACT_WINDOW="$2"; shift 2 ;;
    --setting-sources) need_value "$@"; SETTING_SOURCES="$2"; shift 2 ;;
    --no-hermetic)     HERMETIC=0;        shift ;;
    --no-stop-regress) STOP_ON_REGRESS=0; shift ;;
    --push)            PUSH=1;            shift ;;
    --dry-run)         DRY_RUN=1;         shift ;;
    *) usage_error "Unknown option: $1" ;;
  esac
done

[[ "$MODE" == "plan" || "$MODE" == "build" ]] || usage_error "--mode must be plan or build (got '$MODE')."
if [[ "$MODE" == "plan" ]]; then
  : "${MAX_TURNS:=300}" "${MAX_BUDGET:=60}"
else
  : "${MAX_TURNS:=200}" "${MAX_BUDGET:=30}"
fi
need_int "--max-turns" "$MAX_TURNS"
[[ "$MAX_BUDGET" =~ ^[0-9]+([.][0-9]+)?$ ]] || usage_error "--max-budget must be a number of dollars (got '$MAX_BUDGET')."

# Plan mode writes the whole plan in one session; looping it would rewrite it.
if [[ -z "$MAX_ITERATIONS" ]]; then
  [[ "$MODE" == "plan" ]] && MAX_ITERATIONS=1 || MAX_ITERATIONS=0
fi

# ── Resolve prompt file ───────────────────────────────────────────────────────
if [[ -z "$PROMPT_FILE" ]]; then
  if [[ -z "$CR_NUMBER" ]]; then
    # Try to detect from loop_plan.sh or fall back to initial/
    if [[ -f "loop_plan.sh" ]]; then
      if [[ "$MODE" == "plan" ]]; then
        PROMPT_FILE=$(grep 'PLAN_PROMPT_FILE=' loop_plan.sh | head -1 | sed 's/.*="\(.*\)"/\1/' || true)
      else
        PROMPT_FILE=$(grep 'BUILD_PROMPT_FILE=' loop_plan.sh | head -1 | sed 's/.*="\(.*\)"/\1/' || true)
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

if [[ $DRY_RUN -eq 0 ]] && ! command -v claude >/dev/null 2>&1; then
  usage_error "'claude' CLI not found in PATH."
fi

CURRENT_BRANCH=$(git branch --show-current 2>/dev/null || echo "main")
ITERATION=0
LOG_DIR="log"
RUN_TIMESTAMP=$(date +"%Y%m%d-%H%M%S")

# Resolve progress and plan files (build: context injection + auto-stop; plan: auto-populate after loop)
PROGRESS_FILE=$(infer_progress_file "$PROMPT_FILE")
PLAN_FILE=$(infer_plan_file "$PROMPT_FILE")

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT
TMP_PROMPT="$TMP_DIR/prompt.md"

# ── Claude invocation ─────────────────────────────────────────────────────────
# Project MCP servers only: .ralph/mcp.json, else a .mcp.json committed to the
# repo, else none. An untracked .mcp.json is usually added by an editor
# extension for one developer, so it's left out. This keeps personal servers
# and claude.ai connectors out of loop runs.
resolve_mcp_config() {
  if [[ -f ".ralph/mcp.json" ]]; then
    echo ".ralph/mcp.json"
  elif [[ -f ".mcp.json" ]] && git ls-files --error-unmatch .mcp.json >/dev/null 2>&1; then
    echo ".mcp.json"
  else
    printf '{"mcpServers":{}}\n' > "$TMP_DIR/no-mcp.json"
    echo "$TMP_DIR/no-mcp.json"
  fi
}

CLAUDE_ARGS=(-p --dangerously-skip-permissions --output-format=stream-json --verbose
             --model "$MODEL" --max-turns "$MAX_TURNS" --max-budget-usd "$MAX_BUDGET")
if [[ $HERMETIC -eq 1 ]]; then
  MCP_CONFIG=$(resolve_mcp_config)
  CLAUDE_ARGS+=(--strict-mcp-config --mcp-config "$MCP_CONFIG" --setting-sources "$SETTING_SOURCES")
fi
if [[ "$MODE" == "build" ]]; then
  CONTRACT_FILE="$RALPH_DIR/templates/loop-contract.md"
  CLAUDE_ARGS+=(--agents "$RALPH_DIR/templates/agents/explorer.json"
                --append-system-prompt "$(cat "$CONTRACT_FILE")")
fi

# run_agent <input_file> <log_file>  →  claude's exit status
# Never lets set -e / pipefail end the loop; the caller decides what a failure means.
run_agent() {
  local input_file="$1" log_file="$2" status=0
  local -a env_vars=(CLAUDE_CODE_AUTO_COMPACT_WINDOW="$COMPACT_WINDOW")
  [[ $HERMETIC -eq 1 ]] && env_vars+=(CLAUDE_CODE_DISABLE_AUTO_MEMORY=1)
  env "${env_vars[@]}" claude "${CLAUDE_ARGS[@]}" < "$input_file" 2>&1 | tee "$log_file" || status=$?
  return "$status"
}

# The command line as printed in headers and dry runs (the contract text is long).
describe_command() {
  local arg skip_next=0 out="claude"
  for arg in "${CLAUDE_ARGS[@]}"; do
    if [[ $skip_next -eq 1 ]]; then out+=" <loop-contract.md>"; skip_next=0; continue; fi
    [[ "$arg" == "--append-system-prompt" ]] && skip_next=1
    out+=" $arg"
  done
  echo "$out"
}

# build_prompt <output_file>  →  writes this iteration's prompt
build_prompt() {
  local out="$1"
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" ]]; then
    generate_context "$PROGRESS_FILE" "$PLAN_FILE" > "$out"
    cat "$PROMPT_FILE" >> "$out"
  else
    cat "$PROMPT_FILE" > "$out"
  fi
}

file_chars() { [[ -f "$1" ]] && wc -c < "$1" | tr -d ' ' || echo 0; }

# ── Dry run: show what the next iteration would send, then stop ───────────────
if [[ $DRY_RUN -eq 1 ]]; then
  build_prompt "$TMP_PROMPT"
  SAVED_PROMPT="${TMPDIR:-/tmp}/ralph-dry-run-${RUN_TIMESTAMP}.md"
  cp "$TMP_PROMPT" "$SAVED_PROMPT"
  prompt_chars=$(file_chars "$TMP_PROMPT")
  spec_file="$(dirname "$(dirname "$PROMPT_FILE")")/spec/spec.md"
  old_chars=$(( $(file_chars "$PROMPT_FILE") + $(file_chars "$spec_file") + $(file_chars "${PLAN_FILE:-}") + $(file_chars "$PROGRESS_FILE") ))

  tui_header "start — dry run ($MODE mode)" "nothing is sent to Claude and nothing is written to the project"
  echo "  Unit:     $(select_next_unit "$PROGRESS_FILE")"
  echo "  Prompt:   ${prompt_chars} chars (~$(( prompt_chars / 4 )) tokens), saved to $SAVED_PROMPT"
  if [[ "$MODE" == "build" ]]; then
    echo "  Contract: $(file_chars "$CONTRACT_FILE") chars, added to the system prompt"
    echo ""
    echo "  For comparison, the build prompt plus the files it tells Claude to read"
    echo "  (spec, whole plan, whole progress file) come to ${old_chars} chars (~$(( old_chars / 4 )) tokens)."
  fi
  echo ""
  echo "  Command:  $(describe_command)"
  echo "  Env:      CLAUDE_CODE_AUTO_COMPACT_WINDOW=$COMPACT_WINDOW$([[ $HERMETIC -eq 1 ]] && echo ' CLAUDE_CODE_DISABLE_AUTO_MEMORY=1')"
  exit 0
fi

mkdir -p "$LOG_DIR"

_max_str="unlimited"; [[ $MAX_ITERATIONS -gt 0 ]] && _max_str="max $MAX_ITERATIONS"
_push_str="off (use --push to push after each iteration)"; [[ $PUSH -eq 1 ]] && _push_str="on"
tui_header "start — $MODE mode" "model: $MODEL  |  branch: $CURRENT_BRANCH  |  iterations: $_max_str"
echo "  Per iteration: max $MAX_TURNS turns, max \$$MAX_BUDGET, compaction at $COMPACT_WINDOW tokens"
echo "  Hermetic:      $([[ $HERMETIC -eq 1 ]] && echo "on (MCP: $MCP_CONFIG, settings: $SETTING_SOURCES)" || echo off)"
echo "  Push:          $_push_str"

# ── Loop ──────────────────────────────────────────────────────────────────────
AGENT_FAILURES=0
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
    read -r _c _ip _ns _bl _t <<<"$(index_count_units "$PROGRESS_FILE")"
    [[ "${_t:-0}" -gt 0 ]] && tui_progress "${_c:-0}" "$_t"
  fi
  echo ""

  LOG_FILE="$LOG_DIR/${RUN_TIMESTAMP}-$(basename "$PROMPT_FILE" .md)-iteration-${ITERATION}.log"
  UNIT_ID=""
  [[ "$MODE" == "build" ]] && UNIT_ID=$(select_next_unit "$PROGRESS_FILE")
  build_prompt "$TMP_PROMPT"

  # Snapshot pre-iteration state for the index (never let a snapshot failure
  # take down the whole loop — index recording is best-effort).
  ITER_BEFORE=""
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" ]]; then
    ITER_BEFORE=$(index_snapshot_before "$PROGRESS_FILE" 2>/dev/null || echo "")
  fi

  # Visible "we got to the claude call" marker — Opus + stream-json can take
  # 10–30s to emit its first line, so without this the screen looks frozen.
  tui_log_ok "Invoking claude (model=$MODEL, unit=${UNIT_ID:-n/a}, prompt=$(basename "$PROMPT_FILE"), log=$LOG_FILE)"
  echo "  (first output line typically appears in 10–30s for Opus, 5–10s for Sonnet)"
  echo ""

  AGENT_STATUS=0
  run_agent "$TMP_PROMPT" "$LOG_FILE" || AGENT_STATUS=$?
  SUBTYPE=$(agent_stats "$LOG_FILE" subtype)

  # A capped iteration (turns or budget) is an expected outcome, not a failure:
  # the unit stays in progress and the next iteration resumes it.
  if [[ $AGENT_STATUS -ne 0 && ( "$SUBTYPE" == "error_max_turns" || "$SUBTYPE" == "error_max_budget_usd" ) ]]; then
    tui_log_warn "Iteration $ITERATION hit its cap ($SUBTYPE). The unit stays in progress; the next iteration resumes it."
    AGENT_STATUS=0
  fi

  if [[ $AGENT_STATUS -ne 0 ]]; then
    AGENT_FAILURES=$((AGENT_FAILURES + 1))
    tui_log_warn "claude exited with status $AGENT_STATUS (${SUBTYPE:-no result}); failure $AGENT_FAILURES of $MAX_AGENT_FAILURES in a row. Log: $LOG_FILE"
    if [[ $AGENT_FAILURES -ge $MAX_AGENT_FAILURES ]]; then
      tui_log_err "Stopping: claude failed $MAX_AGENT_FAILURES times in a row. Check the last log, then run 'ralph start' again."
      ITERATION=$((ITERATION + 1))
      break
    fi
    wait_s=$(( RETRY_DELAY * (1 << (AGENT_FAILURES - 1)) ))
    echo "  Retrying in ${wait_s}s…"
    sleep "$wait_s"
    ITERATION=$((ITERATION + 1))
    continue
  fi
  AGENT_FAILURES=0

  # One-line summary of the iteration, from the stream-json result.
  if [[ -f "$LOG_FILE" ]]; then
    _turns=$(agent_stats "$LOG_FILE" num_turns)
    _ms=$(agent_stats "$LOG_FILE" duration_ms)
    _cost=$(agent_stats "$LOG_FILE" total_cost_usd)
    _peak=$(agent_stats "$LOG_FILE" peak_context)
    _compactions=$(agent_stats "$LOG_FILE" compactions)
    if [[ -n "$_turns" ]]; then
      if [[ ${_ms:-0} -lt 60000 ]]; then _took="$(( ${_ms:-0} / 1000 ))s"; else _took="$(( ${_ms:-0} / 60000 )) min"; fi
      _cost_str=$(printf '%.2f' "${_cost:-0}" 2>/dev/null || echo "?")
      tui_log_ok "Iteration $ITERATION: unit ${UNIT_ID:-n/a} · ${_turns} turns · ${_took} · \$${_cost_str} · peak context ${_peak:-?} tokens · ${_compactions:-0} compactions"
    fi
  fi

  # Push only when asked — sync is the developer's decision.
  if [[ $PUSH -eq 1 ]]; then
    if git remote get-url origin >/dev/null 2>&1; then
      git push origin "$CURRENT_BRANCH" 2>/dev/null \
        || git push -u origin "$CURRENT_BRANCH" \
        || tui_log_warn "Push failed; continuing. Push manually when ready."
    else
      tui_log_warn "--push was given but there is no 'origin' remote; skipping the push."
    fi
  fi

  # Record structured iteration index entry (build mode only)
  if [[ "$MODE" == "build" && -n "$PROGRESS_FILE" && -n "$ITER_BEFORE" ]]; then
    index_record_after "$ITERATION" "$MODE" "${CR_NUMBER:-initial}" "$MODEL" \
      "$PROMPT_FILE" "$LOG_FILE" "$PROGRESS_FILE" "$ITER_BEFORE" "$UNIT_ID"

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
