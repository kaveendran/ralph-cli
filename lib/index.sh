#!/usr/bin/env bash
# lib/index.sh — append-only structured iteration index at log/index.jsonl.
# One JSON line per iteration. Read by `ralph watch`, `ralph retro`, and stall
# detection in `ralph start`.

# index_count_units <progress_file>  →  echoes "complete in_progress not_started blocked total"
index_count_units() {
  local f="$1" c=0 ip=0 ns=0 bl=0
  if [[ -f "$f" ]]; then
    c=$(grep  -c '^\- \[x\]'  "$f" 2>/dev/null || true)
    ip=$(grep -c '^\- \[~\]'  "$f" 2>/dev/null || true)
    ns=$(grep -c '^\- \[ \]'  "$f" 2>/dev/null || true)
    bl=$(grep -c '^\- \[!\]'  "$f" 2>/dev/null || true)
  fi
  echo "${c:-0} ${ip:-0} ${ns:-0} ${bl:-0} $(( ${c:-0} + ${ip:-0} + ${ns:-0} + ${bl:-0} ))"
}

# index_snapshot_before <progress_file>  →  echoes "complete|inprog|notstart|blocked|total|head|ts"
# Captured before claude runs; passed to index_record_after.
index_snapshot_before() {
  local pf="$1"
  read -r c ip ns bl t <<<"$(index_count_units "$pf")"
  local head ts
  head=$(git rev-parse HEAD 2>/dev/null || echo "none")
  ts=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
  echo "${c}|${ip}|${ns}|${bl}|${t}|${head}|${ts}"
}

# json_escape <string>  →  stdout: minimally-escaped JSON string content
json_escape() {
  local s="${1:-}"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/}"
  s="${s//$'\t'/\\t}"
  printf '%s' "$s"
}

# index_record_after <iter> <mode> <cr> <model> <prompt> <log_file> <progress_file> <before_snap>
# Appends one JSON line to log/index.jsonl.
index_record_after() {
  local iter="$1" mode="$2" cr="$3" model="$4" prompt_file="$5"
  local log_file="$6" progress_file="$7" before="$8"

  local before_c before_ip before_ns before_bl before_t before_head before_ts
  IFS='|' read -r before_c before_ip before_ns before_bl before_t before_head before_ts <<<"$before"

  read -r c ip ns bl t <<<"$(index_count_units "$progress_file")"

  local head_after ts_after
  head_after=$(git rev-parse HEAD 2>/dev/null || echo "none")
  ts_after=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

  # Git delta — only meaningful if HEAD moved
  local files_changed=0 insertions=0 deletions=0
  if [[ "$before_head" != "none" && "$head_after" != "none" && "$before_head" != "$head_after" ]]; then
    local stat
    stat=$(git diff --shortstat "$before_head".."$head_after" 2>/dev/null || echo "")
    files_changed=$(echo "$stat" | grep -oE '[0-9]+ files? changed' | grep -oE '[0-9]+' || echo "0")
    insertions=$(echo "$stat" | grep -oE '[0-9]+ insertions?' | grep -oE '[0-9]+' || echo "0")
    deletions=$(echo "$stat" | grep -oE '[0-9]+ deletions?' | grep -oE '[0-9]+' || echo "0")
  fi

  # Test commands grep — looks for known layered runners in log
  local tests_seen="["
  local first=1
  for cmd in "scripts/test-unit.sh" "scripts/test-api.sh" "scripts/test-e2e.sh" "pytest" "vitest"; do
    if [[ -f "$log_file" ]] && grep -qF "$cmd" "$log_file" 2>/dev/null; then
      [[ $first -eq 1 ]] && first=0 || tests_seen+=","
      tests_seen+="\"$(json_escape "$cmd")\""
    fi
  done
  tests_seen+="]"

  # Stall / regression detection vs previous iteration in index
  local stalled=false regressed=false
  local complete_delta=$(( c - before_c ))
  local files_delta=$files_changed
  if [[ $complete_delta -eq 0 && $files_delta -eq 0 ]]; then
    stalled=true
  fi
  [[ $complete_delta -lt 0 ]] && regressed=true

  mkdir -p "$(dirname "$log_file")"
  local index_file
  index_file="$(dirname "$log_file")/index.jsonl"

  printf '{"iter":%d,"ts":"%s","mode":"%s","cr":"%s","model":"%s","prompt":"%s","log":"%s","units":{"complete":%d,"in_progress":%d,"not_started":%d,"blocked":%d,"total":%d},"delta":{"complete":%d,"files_changed":%s,"insertions":%s,"deletions":%s},"tests_seen":%s,"head_before":"%s","head_after":"%s","stalled":%s,"regressed":%s}\n' \
    "$iter" \
    "$(json_escape "$ts_after")" \
    "$(json_escape "$mode")" \
    "$(json_escape "$cr")" \
    "$(json_escape "$model")" \
    "$(json_escape "$(basename "$prompt_file")")" \
    "$(json_escape "$(basename "$log_file")")" \
    "$c" "$ip" "$ns" "$bl" "$t" \
    "$complete_delta" "$files_changed" "$insertions" "$deletions" \
    "$tests_seen" \
    "$(json_escape "$before_head")" \
    "$(json_escape "$head_after")" \
    "$stalled" "$regressed" \
    >> "$index_file"
}

# index_recent_stalls <count>  →  echoes count of consecutive stalled iterations from tail
index_recent_stalls() {
  local n="${1:-3}"
  local index_file="log/index.jsonl"
  [[ ! -f "$index_file" ]] && { echo 0; return; }
  # Count trailing consecutive `"stalled":true` lines
  tail -n "$n" "$index_file" 2>/dev/null \
    | grep -c '"stalled":true' || echo 0
}
