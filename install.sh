#!/usr/bin/env bash
# install.sh — put `ralph` on your PATH. Needs Python 3.11+, git and Claude Code.
set -euo pipefail

RALPH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RALPH_BIN="$RALPH_DIR/ralph.sh"
chmod +x "$RALPH_BIN"

if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "ralph needs Python 3.11 or newer as python3; install it first." >&2
  exit 1
fi

echo "Install ralph from $RALPH_DIR:"
echo "  1) symlink into ~/.local/bin (no sudo; that folder must be on your PATH)"
echo "  2) symlink into /usr/local/bin (asks for sudo)"
echo "  3) nothing: run $RALPH_BIN directly"
read -rp "Choice [1]: " CHOICE

case "${CHOICE:-1}" in
  1)
    mkdir -p "$HOME/.local/bin"
    ln -sf "$RALPH_BIN" "$HOME/.local/bin/ralph"
    echo "Linked ~/.local/bin/ralph -> $RALPH_BIN"
    case ":$PATH:" in
      *":$HOME/.local/bin:"*) ;;
      *) echo "Add it to your PATH, e.g.: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;;
    esac
    ;;
  2)
    sudo ln -sf "$RALPH_BIN" /usr/local/bin/ralph
    echo "Linked /usr/local/bin/ralph -> $RALPH_BIN"
    ;;
  3) ;;
  *) echo "Invalid choice." >&2; exit 1 ;;
esac

echo ""
echo "Check the setup with: ralph doctor"
