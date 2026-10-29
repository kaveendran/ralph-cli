#!/usr/bin/env bash
# ralph — a meta-harness for Claude Code. Every command is in the Python
# package next to this script (ralph/); run `ralph --help` for the list.

# Resolve the package dir, following symlinks (e.g. ~/.local/bin/ralph).
# Kept bash-3.2 compatible for stock macOS bash.
RALPH_SOURCE="${BASH_SOURCE[0]}"
while [ -L "$RALPH_SOURCE" ]; do
  RALPH_LINK_DIR="$(cd -P "$(dirname "$RALPH_SOURCE")" && pwd)"
  RALPH_SOURCE="$(readlink "$RALPH_SOURCE")"
  [ "${RALPH_SOURCE#/}" = "$RALPH_SOURCE" ] && RALPH_SOURCE="$RALPH_LINK_DIR/$RALPH_SOURCE"
done
RALPH_DIR="$(cd -P "$(dirname "$RALPH_SOURCE")" && pwd)"

PYTHON="$(command -v python3 || true)"
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
  echo "ralph needs Python 3.11 or newer as python3 on your PATH." >&2
  exit 1
fi
# -P: don't put the current directory on sys.path, so a project's own
# modules can never shadow the harness's.
PYTHONPATH="$RALPH_DIR${PYTHONPATH:+:$PYTHONPATH}" exec "$PYTHON" -P -m ralph "$@"
