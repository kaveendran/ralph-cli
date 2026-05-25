#!/usr/bin/env bash
# Template substitution engine — awk-based, supports multi-line values.
# Usage: apply_template <src> <dst> KEY1 "val1" KEY2 "val2" ...

apply_template() {
  local src="$1" dst="$2"
  shift 2
  cp "$src" "$dst"
  while [[ $# -gt 0 ]]; do
    local key="$1" val="$2"
    # awk gsub treats & as "the matched text" and \ as escape in replacement strings.
    # Pre-escape both so the literal value is output unchanged.
    local escaped
    escaped="${val//\\/\\\\}"   # \ → \\  (must come first)
    escaped="${escaped//&/\\&}" # & → \&
    # Use ENVIRON for the escaped value so newlines in multi-line values are preserved.
    RALPH_TMPL_VAL="$escaped" awk -v k="{{${key}}}" \
      'BEGIN{v=ENVIRON["RALPH_TMPL_VAL"]} { gsub(k, v); print }' \
      "$dst" > "${dst}.tmp" && mv "${dst}.tmp" "$dst"
    shift 2
  done
}

# Verify no placeholders remain after substitution — prints warnings for any {{...}} still in file.
verify_no_placeholders() {
  local file="$1"
  local remaining
  # grep returns 1 when no matches — use || true so set -e doesn't fire.
  remaining=$(grep -oE '\{\{[A-Z_]+\}\}' "$file" 2>/dev/null | sort -u || true)
  if [[ -n "$remaining" ]]; then
    echo "  WARNING: unresolved placeholders in $file:"
    while IFS= read -r p; do
      echo "    $p"
    done <<< "$remaining"
  fi
}
