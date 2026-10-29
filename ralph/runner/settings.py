"""The per-run files and environment that make an agent run hermetic and guarded.

- settings.json: the guardrail hook (ralph/runner/hooks.py) for Bash and edits;
- policy.json: what the hook protects;
- mcp.json: only the project's MCP servers (`.ralph/mcp.json`, or a `.mcp.json`
  committed to the repo), never personal servers or claude.ai connectors;
- environment: the compaction window, auto memory off, and git push disabled
  for the agent's process (a second guard beside the hook; verified in P0).
"""

import json
import shlex
import subprocess
import sys
from pathlib import Path

from ralph.fsutil import write_text_atomic
from ralph.prompts import PACKAGE_DIR

EXPLORER_AGENT = {
    "explorer": {
        "description": ("Read-only codebase explorer. Use it to find files, read code to answer a question, "
                        "trace how something works, or search for usages, instead of reading many files "
                        "yourself. Returns a short summary with file paths and line numbers."),
        "prompt": ("You explore a codebase on behalf of another agent and report back briefly.\n\n"
                   "- Use Glob, Grep and Read to answer the question you were given.\n"
                   "- Read only what you need, in focused line ranges.\n"
                   "- Reply with a summary of at most about 1,500 tokens: the answer, the relevant file paths "
                   "with line numbers, and only the short code excerpts that matter.\n"
                   "- Don't propose or make changes."),
        "tools": ["Read", "Grep", "Glob"],
        "model": "sonnet",
    }
}


def project_mcp_config(root: Path, run_dir: Path) -> Path:
    """The MCP servers a loop run may use: the project's own, or none."""
    own = root / ".ralph" / "mcp.json"
    if own.exists():
        return own
    shared = root / ".mcp.json"
    tracked = subprocess.run(["git", "ls-files", "--error-unmatch", ".mcp.json"], cwd=root,
                             capture_output=True).returncode == 0
    if shared.exists() and tracked:
        return shared
    empty = run_dir / "mcp.json"
    write_text_atomic(empty, '{"mcpServers": {}}\n')
    return empty


def write_run_settings(root: Path, run_dir: Path, frozen: list[str], sandbox: dict | None = None) -> Path:
    """Write settings.json (the hook, and the sandbox when asked for) and policy.json
    (what the hook guards); return settings.json."""
    policy_path = run_dir / "policy.json"
    write_text_atomic(policy_path, json.dumps({"root": str(root), "frozen": frozen}, indent=2) + "\n")
    command = (f"PYTHONPATH={shlex.quote(str(PACKAGE_DIR.parent))} {shlex.quote(sys.executable)} -P "
               f"-m ralph.runner.hooks {shlex.quote(str(policy_path))}")
    hook = {"type": "command", "command": command, "timeout": 30}
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "Bash", "hooks": [hook]},
        {"matcher": "Edit|Write|MultiEdit|NotebookEdit", "hooks": [hook]},
    ]}}
    if sandbox:
        settings["sandbox"] = sandbox
    settings_path = run_dir / "settings.json"
    write_text_atomic(settings_path, json.dumps(settings, indent=2) + "\n")
    return settings_path


def write_agents_file(run_dir: Path) -> Path:
    path = run_dir / "agents.json"
    write_text_atomic(path, json.dumps(EXPLORER_AGENT, indent=2) + "\n")
    return path


def push_disabled_env(root: Path) -> dict[str, str]:
    """Point every remote's push URL at an invalid address, for one process only."""
    remotes = subprocess.run(["git", "remote"], cwd=root, capture_output=True, text=True).stdout.split()
    env = {"GIT_CONFIG_COUNT": str(len(remotes))}
    for index, remote in enumerate(remotes):
        env[f"GIT_CONFIG_KEY_{index}"] = f"remote.{remote}.pushurl"
        env[f"GIT_CONFIG_VALUE_{index}"] = "/dev/null/ralph-push-disabled"
    return env if remotes else {}


def agent_env(root: Path, compact_window: int, hermetic: bool) -> dict[str, str]:
    env = {"CLAUDE_CODE_AUTO_COMPACT_WINDOW": str(compact_window)}
    if hermetic:
        env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] = "1"
    env.update(push_disabled_env(root))
    return env
