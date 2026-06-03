#!/usr/bin/env bash
# lib/tui.sh — TUI wrapper library.
# Uses gum (https://github.com/charmbracelet/gum) when available,
# falls back to plain read/printf otherwise. All commands degrade
# gracefully — nothing breaks if gum is not installed.

_GUM="$(command -v gum 2>/dev/null || true)"
# Set RALPH_NO_TUI=1 to force plain fallback (useful for piped/automated runs).
tui_available() { [[ -z "${RALPH_NO_TUI:-}" ]] && [[ -n "$_GUM" ]]; }

# ── Headers & dividers ─────────────────────────────────────────────────────────

# tui_header <title> [subtitle]
tui_header() {
  local title="$1" subtitle="${2:-}"
  echo ""
  if tui_available; then
    gum style \
      --border rounded \
      --border-foreground 99 \
      --padding "0 2" \
      --bold \
      "ralph — $title"
    [[ -n "$subtitle" ]] && gum style --foreground 245 --margin "0 1" "$subtitle"
  else
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    printf '  %s\n' "$title"
    [[ -n "$subtitle" ]] && printf '  %s\n' "$subtitle"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  fi
  echo ""
}

# tui_section <title>  — lightweight inline section marker
tui_section() {
  if tui_available; then
    echo ""
    gum style --foreground 99 --bold "  $1"
    echo ""
  else
    echo ""
    printf '  ── %s\n' "$1"
    echo ""
  fi
}

# ── Log lines ──────────────────────────────────────────────────────────────────

tui_log_create() {
  if tui_available; then gum log --level info  -- "CREATE  $1"
  else printf '  \033[32mCREATE\033[0m  %s\n' "$1"; fi
}

tui_log_skip() {
  if tui_available; then gum log --level debug -- "SKIP    $1"
  else printf '  \033[33mSKIP  \033[0m  %s\n' "$1"; fi
}

tui_log_ok() {
  if tui_available; then gum log --level info  -- "$1"
  else printf '  \033[32m✓\033[0m  %s\n' "$1"; fi
}

tui_log_warn() {
  if tui_available; then gum log --level warn  -- "$1"
  else printf '  \033[33m!\033[0m  %s\n' "$1" >&2; fi
}

tui_log_err() {
  if tui_available; then gum log --level error -- "$1"
  else printf '  \033[31m✗\033[0m  %s\n' "$1" >&2; fi
}

# ── Interactive inputs ─────────────────────────────────────────────────────────

# tui_input <prompt> [default]  →  stdout: entered value
tui_input() {
  local prompt="$1" default="${2:-}"
  if tui_available; then
    local args=(--prompt "$prompt: ")
    [[ -n "$default" ]] && args+=(--value "$default" --placeholder "$default")
    gum input "${args[@]}"
  else
    local val
    if [[ -n "$default" ]]; then
      read -rp "$prompt [$default]: " val
      printf '%s\n' "${val:-$default}"
    else
      read -rp "$prompt: " val
      printf '%s\n' "$val"
    fi
  fi
}

# tui_choose <header> <opt1> <opt2> ...  →  stdout: chosen option string
tui_choose() {
  local header="$1"; shift
  if tui_available; then
    printf '%s\n' "$@" | gum choose --header "$header"
  else
    printf '%s:\n' "$header" >&2
    local i=1
    for opt in "$@"; do
      printf '  %d) %s\n' "$i" "$opt" >&2
      i=$((i+1))
    done
    local choice
    read -rp "Choice [1]: " choice
    choice="${choice:-1}"
    local idx=0
    for opt in "$@"; do
      idx=$((idx+1))
      [[ "$idx" -eq "$choice" ]] && { printf '%s\n' "$opt"; return; }
    done
    printf '%s\n' "$1"  # default to first
  fi
}

# tui_confirm <prompt> [yes|no]  →  exit 0 (yes) or 1 (no)
# Second arg is the default (defaults to "yes").
tui_confirm() {
  local prompt="$1" default="${2:-yes}"
  if tui_available; then
    if [[ "$default" == "no" ]]; then
      gum confirm --default=false "$prompt"
    else
      gum confirm "$prompt"
    fi
  else
    local ans
    if [[ "$default" == "no" ]]; then
      read -rp "$prompt [y/N]: " ans
      [[ "${ans:-N}" =~ ^[Yy]$ ]]
    else
      read -rp "$prompt [Y/n]: " ans
      [[ "${ans:-Y}" =~ ^[Yy]$ ]]
    fi
  fi
}

# ── Spinner ────────────────────────────────────────────────────────────────────

# tui_spin <title> -- <cmd> [args...]
# Runs cmd with a spinner. Returns cmd exit code.
tui_spin() {
  local title="$1"; shift
  if tui_available; then
    gum spin --title "$title" --spinner dot -- "$@"
  else
    printf '  %s...\n' "$title"
    "$@"
  fi
}

# tui_wait_pids <title> <pid> [pid...]
# Shows a spinner until all given PIDs have exited.
tui_wait_pids() {
  local title="$1"; shift
  local frames=('⠋' '⠙' '⠹' '⠸' '⠼' '⠴' '⠦' '⠧' '⠇' '⠏')
  local i=0

  if tui_available; then
    while true; do
      local any=false
      for p in "$@"; do kill -0 "$p" 2>/dev/null && { any=true; break; }; done
      $any || break
      printf '\r  \033[35m%s\033[0m  %s' "${frames[$((i % 10))]}" "$title"
      sleep 0.3
      i=$((i+1))
    done
    printf '\r  \033[32m✓\033[0m  %s\n' "$title"
  else
    printf '  %s' "$title"
    while true; do
      local any=false
      for p in "$@"; do kill -0 "$p" 2>/dev/null && { any=true; break; }; done
      $any || break
      printf '.'
      sleep 3
    done
    printf '\n'
  fi
}

# ── Progress bar ───────────────────────────────────────────────────────────────

# tui_progress <complete> <total> [label]
tui_progress() {
  local complete="${1:-0}" total="${2:-0}" label="${3:-}"
  [[ "$total" -le 0 ]] && return
  local pct=$(( complete * 100 / total ))
  local filled=$(( complete * 24 / total ))
  local empty=$(( 24 - filled ))
  local bar=""
  local f e
  for (( f=0; f<filled; f++ )); do bar="${bar}█"; done
  for (( e=0; e<empty;  e++ )); do bar="${bar}░"; done
  if tui_available; then
    gum style --foreground 99 "  ${bar}  ${complete}/${total} units  (${pct}%)${label:+  — $label}"
  else
    printf '  %s  %d/%d units  (%d%%)%s\n' \
      "$bar" "$complete" "$total" "$pct" "${label:+  — $label}"
  fi
}

# ── Stage badge ────────────────────────────────────────────────────────────────

# tui_stage_badge <stage>  →  prints colored stage label
tui_stage_badge() {
  local stage="$1"
  if tui_available; then
    case "$stage" in
      1-write-spec)      gum style --foreground 208 --bold "● WRITE SPEC"     ;;
      2-plan)            gum style --foreground 33  --bold "● PLAN"           ;;
      3-ready-to-build)  gum style --foreground 226 --bold "● READY TO BUILD" ;;
      3-build)           gum style --foreground 99  --bold "● BUILDING"       ;;
      4-blocked)         gum style --foreground 196 --bold "● BLOCKED"        ;;
      5-done)            gum style --foreground 82  --bold "● COMPLETE"       ;;
      *)                 gum style --foreground 245 --bold "● $stage"         ;;
    esac
  else
    case "$stage" in
      1-write-spec)      printf '\033[33m● WRITE SPEC\033[0m\n'     ;;
      2-plan)            printf '\033[34m● PLAN\033[0m\n'           ;;
      3-ready-to-build)  printf '\033[33m● READY TO BUILD\033[0m\n' ;;
      3-build)           printf '\033[35m● BUILDING\033[0m\n'       ;;
      4-blocked)         printf '\033[31m● BLOCKED\033[0m\n'        ;;
      5-done)            printf '\033[32m● COMPLETE\033[0m\n'       ;;
      *)                 printf '● %s\n' "$stage"                   ;;
    esac
  fi
}
