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

  # Next not-started unit — prefer highest priority (P0 > P1 > P2 > P3),
  # fall back to file order when no priority tags are present.
  # IMPORTANT: use `grep -m1 ... || true` instead of `grep ... | head -1`.
  # The pipe form crashes under `set -euo pipefail` when grep finds no matches
  # (grep exits 1, pipefail propagates, set -e kills the script silently —
  # which manifests as the start loop exiting right before the claude call).
  if [[ "${not_started:-0}" -gt 0 ]]; then
    local next
    next=$(grep -m1 '^\- \[ \] U[0-9]\+ \[P0' "$progress_file" 2>/dev/null || true)
    [[ -z "$next" ]] && next=$(grep -m1 '^\- \[ \] U[0-9]\+ \[P1' "$progress_file" 2>/dev/null || true)
    [[ -z "$next" ]] && next=$(grep -m1 '^\- \[ \] U[0-9]\+ \[P2' "$progress_file" 2>/dev/null || true)
    [[ -z "$next" ]] && next=$(grep -m1 '^\- \[ \] U[0-9]\+ \[P3' "$progress_file" 2>/dev/null || true)
    [[ -z "$next" ]] && next=$(grep -m1 '^\- \[ \]'                "$progress_file" 2>/dev/null || true)
    next="${next#- [ ] }"
    printf 'Next unit to implement (highest priority unblocked): %s\n\n' "$next"
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
# Returns 1 (false) if the file is missing, unreadable, or has no unit markers.
all_units_done() {
  local progress_file="$1"
  [[ ! -f "$progress_file" ]] && return 1
  local not_done total
  not_done=$(grep -cE '^\- \[(~| |!)\]' "$progress_file" 2>/dev/null || true)
  total=$(grep -cE '^\- \[(x|~| |!)\]' "$progress_file" 2>/dev/null || true)
  # No recognisable unit markers → file is a stub, not actually done
  [[ "${total:-0}" -eq 0 ]] && return 1
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

# Infer the plan file path from a prompt file path (returns first match).
infer_plan_file() {
  local prompt_file="$1"
  local cr_dir f
  cr_dir="$(dirname "$(dirname "$prompt_file")")"
  for f in "${cr_dir}/plan/"*"-implementation-plan.md"; do
    [[ -f "$f" ]] && echo "$f" && return
  done
}

# Populate the progress file from plan unit headers, but only if the progress
# file exists and has no existing unit markers (i.e. it is still a stub).
# Plan format:
#   ## Unit: NAME (ID: UXXX)
#   **Status:** ...
#   **Priority:** P0-Critical | P1-High | P2-Medium | P3-Low
#   **Type:** ...
#   **Size Estimate:** Small | Medium | Large
# Progress fmt:
#   - [ ] UXXX [P0|M] NAME   (priority/size annotation injected when present)
populate_progress_from_plan() {
  local plan_file="$1" progress_file="$2"
  [[ ! -f "$plan_file" ]]     && return
  [[ ! -f "$progress_file" ]] && return

  # Skip if real unit markers already exist — don't overwrite live progress
  local existing
  existing=$(grep -cE '^\- \[(x|~| |!)\]' "$progress_file" 2>/dev/null || true)
  [[ "${existing:-0}" -gt 0 ]] && return

  # Use awk to scan the plan and emit "- [ ] UXXX [Px|S] NAME" per unit,
  # peeking forward up to ~6 lines for **Priority:** and **Size Estimate:**.
  local units
  units=$(awk '
    /^## Unit:/ {
      name=""; id=""; pri=""; sz=""
      if (match($0, /^## Unit: (.+) \(ID: (U[0-9]+)\)/, m)) {
        name=m[1]; id=m[2]
      }
      # peek ahead up to 8 lines for Priority and Size Estimate
      for (i=0; i<8 && (getline line) > 0; i++) {
        if (line ~ /^\*\*Priority:\*\*/) {
          if (match(line, /\*\*Priority:\*\*[[:space:]]*(P[0-3])/, p)) pri=p[1]
        } else if (line ~ /^\*\*Size Estimate:\*\*/) {
          if (match(line, /\*\*Size Estimate:\*\*[[:space:]]*(Small|Medium|Large)/, s)) {
            if (s[1]=="Small") sz="S"; else if (s[1]=="Medium") sz="M"; else if (s[1]=="Large") sz="L"
          }
        } else if (line ~ /^## Unit:/) {
          # next unit reached — emit current, push line back via buffer
          break
        }
      }
      if (id != "") {
        tag=""
        if (pri != "" && sz != "") tag=" [" pri "|" sz "]"
        else if (pri != "")        tag=" [" pri "]"
        else if (sz != "")         tag=" [" sz "]"
        printf("- [ ] %s%s %s\n", id, tag, name)
      }
    }
  ' "$plan_file")

  [[ -z "$units" ]] && return

  local cr_label
  cr_label="$(basename "$(dirname "$(dirname "$progress_file")")")"

  {
    printf '# %s Implementation Progress\n\n' "$cr_label"
    printf '**Release tag:** (pending — set when all units are [x])\n\n'
    printf 'Annotations on each unit (next to the ID) are `[Priority|Size]` carried from the plan, e.g. `[P0|M]` = P0-Critical, Medium size. Pick higher priority first.\n\n'
    printf -- '---\n\n'
    printf '## Units\n\n'
    printf '%s\n' "$units"
    printf '\n'
    printf -- '---\n\n'
    printf '## Notes\n\n'
    printf -- '- Record gaps, ambiguities, and assumptions here as they are resolved.\n'
  } > "$progress_file"
}
