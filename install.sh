#!/usr/bin/env bash
# install.sh — add 'ralph' to your PATH
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RALPH_BIN="$RALPH_DIR/ralph.sh"

chmod +x "$RALPH_BIN"
chmod +x "$RALPH_DIR/commands/"*.sh
chmod +x "$RALPH_DIR/templates/"*.sh

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ralph-cli install"
echo "  Package: $RALPH_DIR"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Choose how to install:"
echo "  1) Add alias to ~/.bashrc  (no sudo, works in Git Bash)"
echo "  2) Symlink to /usr/local/bin  (requires sudo, system-wide)"
echo "  3) Just make scripts executable (use full path: $RALPH_BIN)"
echo ""
read -rp "Choice [1]: " CHOICE
CHOICE="${CHOICE:-1}"

case "$CHOICE" in
  1)
    RC_FILE="$HOME/.bashrc"
    ALIAS_LINE="alias ralph='${RALPH_BIN}'"
    if grep -qF "$ALIAS_LINE" "$RC_FILE" 2>/dev/null; then
      echo "Alias already present in $RC_FILE"
    else
      echo "$ALIAS_LINE" >> "$RC_FILE"
      echo "Added alias to $RC_FILE"
    fi
    echo ""
    echo "  Run: source ~/.bashrc"
    echo "  Then: ralph help"
    ;;
  2)
    sudo ln -sf "$RALPH_BIN" /usr/local/bin/ralph
    echo "Symlinked: /usr/local/bin/ralph -> $RALPH_BIN"
    echo ""
    echo "  Run: ralph help"
    ;;
  3)
    echo "Scripts are executable. Use the full path:"
    echo "  $RALPH_BIN help"
    ;;
  *)
    echo "Invalid choice."
    exit 1
    ;;
esac

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Install complete"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "Optional: install gum for an enhanced TUI (arrow-key menus, spinners, progress bars):"
echo ""
echo "  # macOS / Linux (Homebrew)"
echo "  brew install gum"
echo ""
echo "  # Windows (Winget)"
echo "  winget install charmbracelet.gum"
echo ""
echo "  # Direct download: https://github.com/charmbracelet/gum/releases"
echo ""
echo "  ralph works without gum — it degrades to plain prompts automatically."
