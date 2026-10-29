"""Keep up with Claude Code updates.

Claude Code updates itself often (it went from 2.1.306 to 2.1.311 during
one working session), and the harness depends on its flags and its
stream-json format. A pinned version in `.ralph/config.toml` turns silent
drift into a clear stop.
"""

import re
import shutil
import subprocess

from ralph.errors import RalphError

# Every flag the harness passes. `--max-turns` is real but hidden from --help.
FLAGS_USED = ("--print", "--output-format", "--verbose", "--model", "--max-budget-usd",
              "--dangerously-skip-permissions", "--tools", "--json-schema", "--append-system-prompt",
              "--agents", "--settings", "--strict-mcp-config", "--mcp-config", "--setting-sources")


def find_claude(claude: str = "claude") -> str:
    path = shutil.which(claude)
    if not path:
        raise RalphError("The 'claude' CLI isn't on your PATH.",
                         "Install Claude Code (https://code.claude.com), then run 'ralph doctor'.")
    return path


def installed_version(claude: str = "claude") -> str:
    output = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout
    match = re.search(r"\d+\.\d+\.\d+", output)
    return match.group(0) if match else ""


def check_version(pinned: str, claude: str = "claude", allow_drift: bool = False) -> str:
    """The installed version; raises when it differs from the pin (unless allowed)."""
    version = installed_version(claude)
    if pinned and version != pinned and not allow_drift:
        raise RalphError(
            f"Claude Code is {version or 'unknown'}, but .ralph/config.toml pins {pinned}.",
            "Run 'ralph doctor' to check the new version still works, then update runner.claude_version "
            "— or pass --allow-version-drift for this run. To stop automatic updates, set "
            "DISABLE_AUTOUPDATER=1 (see https://code.claude.com/docs/en/setup).")
    return version


def missing_flags(claude: str = "claude") -> list[str]:
    """Flags the harness uses that this Claude Code's --help doesn't mention."""
    text = subprocess.run([claude, "--help"], capture_output=True, text=True).stdout
    return [flag for flag in FLAGS_USED if flag not in text]
