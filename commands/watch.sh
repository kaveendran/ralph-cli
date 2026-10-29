#!/usr/bin/env bash
# ralph watch — live structured tail of the running loop.
# Two display channels:
#   1. log/index.jsonl  → one line per iteration (units, delta, stalls, regressions)
#   2. log/<latest>.log → current iteration's stream-json events (tool calls, edits, tests)
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$RALPH_DIR/lib/tui.sh"

MODE="both"   # both | index | log
FOLLOW=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --index-only) MODE="index"; shift ;;
    --log-only)   MODE="log";   shift ;;
    --no-follow)  FOLLOW=0;     shift ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

LOG_DIR="log"
INDEX="$LOG_DIR/index.jsonl"

if [[ ! -d "$LOG_DIR" ]]; then
  echo "No log/ directory yet. Run 'ralph start' first."
  exit 1
fi

# ── Find the most recent iteration log file ───────────────────────────────────
latest_log() {
  ls -t "$LOG_DIR"/*-iteration-*.log 2>/dev/null | head -1 || true
}

# ── Pretty-print a single index.jsonl line ────────────────────────────────────
render_index_line() {
  local line="$1"
  [[ -z "$line" ]] && return

  # Minimal field extraction — tolerant of awk being the only thing available.
  local iter ts c ip ns bl t cdelta fc stalled regressed
  iter=$(echo "$line" | grep -oE '"iter":[0-9]+' | head -1 | grep -oE '[0-9]+')
  ts=$(echo "$line"   | grep -oE '"ts":"[^"]*"' | head -1 | sed 's/"ts":"\(.*\)"/\1/')
  c=$(echo "$line"    | grep -oE '"complete":[0-9]+' | head -1 | grep -oE '[0-9]+')
  ip=$(echo "$line"   | grep -oE '"in_progress":[0-9]+' | head -1 | grep -oE '[0-9]+')
  ns=$(echo "$line"   | grep -oE '"not_started":[0-9]+' | head -1 | grep -oE '[0-9]+')
  bl=$(echo "$line"   | grep -oE '"blocked":[0-9]+' | head -1 | grep -oE '[0-9]+')
  t=$(echo "$line"    | grep -oE '"total":[0-9]+' | head -1 | grep -oE '[0-9]+')
  cdelta=$(echo "$line" | sed -n 's/.*"delta":{"complete":\(-\?[0-9]*\).*/\1/p')
  fc=$(echo "$line"   | grep -oE '"files_changed":[0-9]+' | head -1 | grep -oE '[0-9]+')
  stalled=$(echo "$line" | grep -oE '"stalled":(true|false)' | head -1 | sed 's/"stalled"://')
  regressed=$(echo "$line" | grep -oE '"regressed":(true|false)' | head -1 | sed 's/"regressed"://')

  local flag="  "
  [[ "$regressed" == "true" ]] && flag="\033[31m✗R\033[0m"
  [[ "$stalled"   == "true" ]] && flag="\033[33m·S\033[0m"
  [[ "${cdelta:-0}" -gt 0 ]]   && flag="\033[32m+$cdelta\033[0m"

  printf '  iter %2s  %s  %s   %s/%s done  (~%s, ✗%s, ▢%s)  files:%s\n' \
    "${iter:-?}" "${ts:-?}" "$(printf '%b' "$flag")" \
    "${c:-0}" "${t:-0}" "${ip:-0}" "${bl:-0}" "${ns:-0}" "${fc:-0}"
}

# ── Pretty-print an iteration-log stream-json line ────────────────────────────
# Only surfaces high-signal events; everything else is dropped silently.
render_log_line() {
  local line="$1"
  [[ -z "$line" ]] && return

  # Tool use — Edit / Write / Bash / Read
  if echo "$line" | grep -q '"type":"tool_use"'; then
    local tool path cmd
    tool=$(echo "$line" | grep -oE '"name":"[^"]+"' | head -1 | sed 's/"name":"\(.*\)"/\1/')
    case "$tool" in
      Edit|Write|NotebookEdit)
        path=$(echo "$line" | grep -oE '"file_path":"[^"]*"' | head -1 | sed 's/"file_path":"\(.*\)"/\1/')
        printf '  \033[36m%-6s\033[0m  %s\n' "$tool" "${path:-?}"
        ;;
      Read)
        path=$(echo "$line" | grep -oE '"file_path":"[^"]*"' | head -1 | sed 's/"file_path":"\(.*\)"/\1/')
        printf '  \033[90mRead\033[0m    %s\n' "${path:-?}"
        ;;
      Bash|PowerShell)
        cmd=$(echo "$line" | grep -oE '"command":"[^"]{0,120}' | head -1 | sed 's/"command":"//')
        printf '  \033[35m%-6s\033[0m  %s\n' "$tool" "${cmd:-?}"
        ;;
      Task|Agent)
        local desc
        desc=$(echo "$line" | grep -oE '"description":"[^"]*"' | head -1 | sed 's/"description":"\(.*\)"/\1/')
        printf '  \033[95mAgent\033[0m   %s\n' "${desc:-(subagent)}"
        ;;
    esac
    return
  fi

  # Test output / commit / tag references in assistant text
  if echo "$line" | grep -qE 'passed|failed|FAILED|PASSED|✓|✗' \
     && echo "$line" | grep -q '"type":"text"'; then
    local snippet
    snippet=$(echo "$line" | grep -oE '"text":"[^"]{0,140}' | head -1 | sed 's/"text":"//')
    [[ -n "$snippet" ]] && printf '  \033[33mTest\033[0m    %s\n' "$snippet"
  fi
}

# ── Render header ─────────────────────────────────────────────────────────────
tui_header "watch — live loop tail" "follow=${FOLLOW}  mode=${MODE}"

# Show last few index entries for context
if [[ -f "$INDEX" && "$MODE" != "log" ]]; then
  echo "Recent iterations:"
  tail -n 5 "$INDEX" 2>/dev/null | while IFS= read -r line; do
    render_index_line "$line"
  done
  echo ""
fi

if [[ $FOLLOW -eq 0 ]]; then
  exit 0
fi

CURRENT_LOG="$(latest_log)"
if [[ -z "$CURRENT_LOG" ]]; then
  echo "No iteration log to follow yet. Will wait for one to appear."
fi

INDEX_LAST_SIZE=0
[[ -f "$INDEX" ]] && INDEX_LAST_SIZE=$(wc -c < "$INDEX" 2>/dev/null || echo 0)

LOG_LAST_SIZE=0
[[ -n "$CURRENT_LOG" && -f "$CURRENT_LOG" ]] && LOG_LAST_SIZE=$(wc -c < "$CURRENT_LOG" 2>/dev/null || echo 0)

echo ""
echo "Watching... (Ctrl-C to exit)"
echo ""

while true; do
  # Check for new index entries
  if [[ -f "$INDEX" && "$MODE" != "log" ]]; then
    NEW_SIZE=$(wc -c < "$INDEX" 2>/dev/null || echo 0)
    if [[ "$NEW_SIZE" -gt "$INDEX_LAST_SIZE" ]]; then
      tail -c "+$((INDEX_LAST_SIZE + 1))" "$INDEX" 2>/dev/null | while IFS= read -r line; do
        echo ""
        gum style --foreground 99 --bold "  ── iteration boundary ──" 2>/dev/null \
          || echo "  ── iteration boundary ──"
        render_index_line "$line"
        echo ""
      done
      INDEX_LAST_SIZE=$NEW_SIZE
    fi
  fi

  # Track newest log file (it changes between iterations)
  NEW_LATEST="$(latest_log)"
  if [[ "$NEW_LATEST" != "$CURRENT_LOG" ]]; then
    CURRENT_LOG="$NEW_LATEST"
    LOG_LAST_SIZE=0
    [[ -n "$CURRENT_LOG" ]] && echo "  → following: $CURRENT_LOG"
  fi

  # Stream new lines from the active iteration log
  if [[ -n "$CURRENT_LOG" && -f "$CURRENT_LOG" && "$MODE" != "index" ]]; then
    NEW_SIZE=$(wc -c < "$CURRENT_LOG" 2>/dev/null || echo 0)
    if [[ "$NEW_SIZE" -gt "$LOG_LAST_SIZE" ]]; then
      tail -c "+$((LOG_LAST_SIZE + 1))" "$CURRENT_LOG" 2>/dev/null | while IFS= read -r line; do
        render_log_line "$line"
      done
      LOG_LAST_SIZE=$NEW_SIZE
    fi
  fi

  sleep 1
done
