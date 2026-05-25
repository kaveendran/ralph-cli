#!/usr/bin/env bash
# Generates a compact iteration-state header prepended to each loop prompt.
# Shortcircuits Claude re-analyzing the full progress file from scratch.

generate_context() {
  local progress_file="$1"
  local complete=0 in_progress=0 blocked=0 not_started=0 total=0

  if [[ -f "$progress_file" ]]; then
    complete=$(grep -c '^\- \[x\]'  "$progress_file" 2>/dev/null || true)
    in_progress=$(grep -c '^\- \[~\]' "$progress_file" 2>/dev/null || true)
    blocked=$(grep -c '^\- \[!\]'   "$progress_file" 2>/dev/null || true)
    not_started=$(grep -c '^\- \[ \]' "$progress_file" 2>/dev/null || true)
    total=$((complete + in_progress + blocked + not_started))
  fi

  printf '## Loop State (auto-generated — read this first)\n\n'

  if [[ "$total" -gt 0 ]]; then
    local pct=$(( complete * 100 / total ))
    printf 'Progress: %d/%d units complete (%d%%)\n\n' "$complete" "$total" "$pct"
  fi

  # In-progress units — resume these before starting anything new
  if [[ "${in_progress:-0}" -gt 0 ]]; then
    printf 'Resume first (in-progress):\n'
    grep '^\- \[~\]' "$progress_file" 2>/dev/null | head -3 | sed 's/^/  /'
    printf '\n'
  fi

  # Next not-started unit — what to pick up
  if [[ "${not_started:-0}" -gt 0 ]]; then
    local next
    next=$(grep '^\- \[ \]' "$progress_file" 2>/dev/null | head -1 | sed 's/^\- \[ \] //')
    printf 'Next unit to implement: %s\n\n' "$next"
  elif [[ "${in_progress:-0}" -eq 0 && "${blocked:-0}" -eq 0 && "$total" -gt 0 ]]; then
    printf 'All units complete. Verify acceptance criteria then create the git tag.\n\n'
  fi

  # Blocked units — re-evaluate before skipping
  if [[ "${blocked:-0}" -gt 0 ]]; then
    printf 'Blocked — re-read each unit'\''s Notes before skipping. If the blocker is resolved, change [!] to [ ] and implement it. Otherwise skip.\n'
    grep '^\- \[!\]' "$progress_file" 2>/dev/null | head -5 | sed 's/^/  /'
    printf '\n'
  fi

  # Last commit — gives Claude grounding on what already landed
  local last_commit
  last_commit=$(git log --oneline -1 2>/dev/null || echo "(no commits yet)")
  printf 'Last commit: %s\n' "$last_commit"

  printf '\n---\n\n'
}

# Returns 0 (true) when every unit in the progress file is marked [x].
all_units_done() {
  local progress_file="$1"
  [[ ! -f "$progress_file" ]] && return 1
  local not_done
  not_done=$(grep -cE '^\- \[(~| |!)\]' "$progress_file" 2>/dev/null || true)
  [[ "${not_done:-1}" -eq 0 ]] && return 0 || return 1
}

# Infer the progress file path from a prompt file path.
# development/cr5/prompts/build.md → development/cr5/progress/implementation-progress.md
infer_progress_file() {
  local prompt_file="$1"
  local cr_dir
  cr_dir="$(dirname "$(dirname "$prompt_file")")"
  echo "${cr_dir}/progress/implementation-progress.md"
}
