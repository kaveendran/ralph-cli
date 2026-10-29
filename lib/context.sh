#!/usr/bin/env bash
# Generates a compact iteration-state header prepended to each loop prompt.
# Shortcircuits Claude re-analyzing the full progress file from scratch.
#
# All awk here is POSIX (no gawk-only 3-argument match) so it runs on the
# BSD awk that ships with macOS.

# A unit line in a progress file: "- [x] U012 …" or "- [x] **U012 …**".
# Indented sample lines (inside the template's HTML comment) don't match.
UNIT_LINE_RE='^- \[(x|~| |!)\] (\*\*)?U[0-9]+'

# The placeholder line the progress template ships with before planning.
PROGRESS_STUB_TEXT='fill in after running plan mode'

# count_units <progress_file> <marker>  →  number of unit lines with that marker
# marker is one of: x  ~  " "  !
count_units() {
  local progress_file="$1" marker="$2"
  [[ -f "$progress_file" ]] || { echo 0; return; }
  local count
  count=$(grep -cE "^- \[${marker}\] (\*\*)?U[0-9]+" "$progress_file" 2>/dev/null || true)
  echo "${count:-0}"
}

# select_next_unit <progress_file>  →  the unit ID to work on (empty when none)
# Order: resume an in-progress unit, then the highest-priority not-started unit
# (P0 > P1 > P2 > P3, from the [P0|M] tags), then file order. Blocked units are
# skipped.
select_next_unit() {
  local progress_file="$1" line="" pri
  [[ -f "$progress_file" ]] || return 0
  line=$(grep -m1 -E '^- \[~\] (\*\*)?U[0-9]+' "$progress_file" 2>/dev/null || true)
  for pri in P0 P1 P2 P3; do
    [[ -n "$line" ]] && break
    line=$(grep -m1 -E "^- \[ \] (\*\*)?U[0-9]+ \[${pri}" "$progress_file" 2>/dev/null || true)
  done
  [[ -z "$line" ]] && line=$(grep -m1 -E '^- \[ \] (\*\*)?U[0-9]+' "$progress_file" 2>/dev/null || true)
  printf '%s\n' "$line" | grep -oE 'U[0-9]+' | head -1 || true
}

# extract_unit_section <plan_file> <unit_id>  →  stdout: that unit's plan entry
# A section starts at "## Unit: … (ID: U037)" and ends at the next level-2
# heading, so the agent gets its own unit without reading the whole plan.
extract_unit_section() {
  local plan_file="$1" unit_id="$2"
  [[ -f "$plan_file" && -n "$unit_id" ]] || return 0
  awk -v id="(ID: ${unit_id})" '
    /^## / {
      if (inside) exit
      if (index($0, "## Unit:") == 1 && index($0, id) > 0) inside = 1
    }
    inside { print }
  ' "$plan_file"
}

generate_context() {
  local progress_file="$1" plan_file="${2:-}"
  local complete=0 in_progress=0 blocked=0 not_started=0 total=0

  if [[ -f "$progress_file" ]]; then
    complete=$(count_units "$progress_file" x)
    in_progress=$(count_units "$progress_file" '~')
    blocked=$(count_units "$progress_file" '!')
    not_started=$(count_units "$progress_file" ' ')
    total=$((complete + in_progress + blocked + not_started))
  fi

  printf '## Loop State (auto-generated — read this first)\n\n'

  if [[ "$total" -gt 0 ]]; then
    local pct=$(( complete * 100 / total ))
    printf 'Progress: %d/%d units complete (%d%%)\n\n' "$complete" "$total" "$pct"
  fi

  # The one unit this iteration works on. Everything else is context only.
  local unit unit_re
  unit=$(select_next_unit "$progress_file")
  unit_re="^- \[.\] (\*\*)?${unit:-NONE}([^0-9]|$)"   # this unit's own line, and nothing longer
  if [[ -n "$unit" ]]; then
    if grep -qE "^- \[~\] (\*\*)?${unit}([^0-9]|$)" "$progress_file" 2>/dev/null; then
      printf "This iteration's unit: %s (in progress — resume it)\n\n" "$unit"
    else
      printf "This iteration's unit: %s\n\n" "$unit"
    fi
  elif [[ "${in_progress:-0}" -eq 0 && "${blocked:-0}" -eq 0 && "$total" -gt 0 ]]; then
    printf 'All units complete. Verify acceptance criteria then create the git tag.\n\n'
  fi

  # Other in-progress units, for awareness only.
  if [[ "${in_progress:-0}" -gt 1 ]]; then
    printf 'Also in progress (not for this iteration):\n'
    { grep -E '^- \[~\] (\*\*)?U[0-9]+' "$progress_file" 2>/dev/null | grep -vE "$unit_re" | head -3 | sed 's/^/  /'; } || true
    printf '\n'
  fi

  # The unit after this one, so the agent knows where the work is heading.
  # IMPORTANT: every pipeline here ends in `|| true`. Without it, a grep that
  # finds nothing exits 1, pipefail propagates it, and set -e kills the script
  # silently — which shows up as the start loop exiting right before the
  # claude call.
  if [[ "${not_started:-0}" -gt 0 ]]; then
    local next
    next=$(grep -E '^- \[ \] (\*\*)?U[0-9]+' "$progress_file" 2>/dev/null | grep -vE "$unit_re" | head -1 || true)
    next="${next#"- [ ] "}"   # quoted: a literal prefix, not a glob
    [[ -n "$next" ]] && printf 'After this unit (a later iteration, not now): %s\n\n' "$next"
  fi

  # Blocked units — listed so they aren't picked up by accident.
  if [[ "${blocked:-0}" -gt 0 ]]; then
    printf 'Blocked — not for this iteration. A human re-checks these once the blocker is resolved:\n'
    grep -E '^- \[!\] (\*\*)?U[0-9]+' "$progress_file" 2>/dev/null | head -5 | sed 's/^/  /'
    printf '\n'
  fi

  # Last commit — gives Claude grounding on what already landed
  local last_commit
  last_commit=$(git log --oneline -1 2>/dev/null || echo "(no commits yet)")
  printf 'Last commit: %s\n' "$last_commit"

  # The unit's own plan entry, so the agent doesn't read the whole plan.
  if [[ -n "$unit" && -n "$plan_file" ]]; then
    local section
    section=$(extract_unit_section "$plan_file" "$unit")
    if [[ -n "$section" ]]; then
      printf '\n## Your unit: plan entry for %s\n\n' "$unit"
      printf "This is your unit's complete entry from %s. You don't need to read the rest of the plan.\n\n" "$plan_file"
      printf '%s\n' "$section"
    fi
  fi

  printf '\n---\n\n'
}

# Returns 0 (true) when every unit in the progress file is marked [x].
# Returns 1 (false) if the file is missing, unreadable, or has no unit markers.
all_units_done() {
  local progress_file="$1"
  [[ ! -f "$progress_file" ]] && return 1
  local not_done total
  not_done=$(grep -cE '^- \[(~| |!)\] (\*\*)?U[0-9]+' "$progress_file" 2>/dev/null || true)
  total=$(grep -cE "$UNIT_LINE_RE" "$progress_file" 2>/dev/null || true)
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
# file exists and has no real unit markers (i.e. it is still a stub — the
# template's "fill in after running plan mode" line doesn't count).
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
  existing=$(grep -E "$UNIT_LINE_RE" "$progress_file" 2>/dev/null \
    | grep -vc "$PROGRESS_STUB_TEXT" || true)
  [[ "${existing:-0}" -gt 0 ]] && return

  # Scan the plan once. Each "## Unit:" header starts a unit; its Priority and
  # Size lines are read until the next level-2 heading ends it.
  local units
  units=$(awk '
    function flush(  tag) {
      if (id != "") {
        tag = ""
        if (pri != "" && sz != "") tag = " [" pri "|" sz "]"
        else if (pri != "")        tag = " [" pri "]"
        else if (sz != "")         tag = " [" sz "]"
        printf("- [ ] %s%s %s\n", id, tag, name)
      }
      id = ""; name = ""; pri = ""; sz = ""
    }
    /^## Unit:/ {
      flush()
      p = index($0, "(ID: ")
      if (p > 0) {
        rest = substr($0, p + 5)
        q = index(rest, ")")
        if (q > 0) id = substr(rest, 1, q - 1)
        name = substr($0, 10, p - 11)   # text between "## Unit: " and " (ID:"
      }
      next
    }
    /^## / { flush(); next }
    id != "" && /^\*\*Priority:\*\*/ {
      if (match($0, /P[0-3]/)) pri = substr($0, RSTART, RLENGTH)
    }
    id != "" && /^\*\*Size Estimate:\*\*/ {
      if (match($0, /Small|Medium|Large/)) sz = substr($0, RSTART, 1)
    }
    END { flush() }
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
