"""The guardrail hook: stops the agent from doing the harness's job by accident.

Claude Code runs this before each tool call (a `PreToolUse` hook) and passes
the call as JSON on stdin. It denies:
- git commands that change history, branches or remotes (the harness commits
  locally; syncing is the developer's job);
- edits to harness state: `.ralph/`, `development/*/units/`, progress files;
- edits to frozen or locked paths from the project config.

This prevents accidents, not deliberate misuse. A script that runs git itself
gets past a pattern match. The security boundary is a container or the OS
sandbox. A deny holds even under --dangerously-skip-permissions (verified in
P0, docs/meta-harness-plan.md §20).

Usage, as configured by ralph/runner/settings.py:
    python3 -P -m ralph.runner.hooks <policy.json>   (hook JSON on stdin)
"""

import fnmatch
import json
import re
import sys
from pathlib import Path, PurePosixPath

DENIED_GIT = ("commit", "push", "pull", "fetch", "merge", "rebase", "reset", "checkout", "switch",
              "stash", "tag", "cherry-pick", "revert", "am", "worktree", "remote", "restore")
ALWAYS_PROTECTED = (".ralph/**", "development/*/units/**", "development/*/progress/**",
                    "development/*/PROGRESS.md")
_GIT_CALL = re.compile(r"(?:^|[\s;&|(`$])git(?:\s+-[cC]\s+\S+)*\s+([a-z-]+)(?P<rest>[^;&|]*)")
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _denied_git(command: str) -> str | None:
    for match in _GIT_CALL.finditer(command):
        subcommand, rest = match.group(1), match.group("rest")
        if subcommand in DENIED_GIT:
            return subcommand
        if subcommand == "config" and not re.search(r"--(get|list)\b|\s-l\b", rest):
            return "config"
        if subcommand == "branch" and re.search(r"\s-[dDmMfc]\b|--(delete|move|force|copy)\b", rest):
            return "branch"
    return None


def _relative(path: str, root: Path) -> str:
    candidate = Path(path)
    if candidate.is_absolute():
        try:
            candidate = candidate.resolve().relative_to(root.resolve())
        except ValueError:
            return str(PurePosixPath(candidate))
    return str(PurePosixPath(candidate))


def _matches(path: str, patterns) -> str | None:
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(path, pattern.rstrip("/*") + "/*") \
                or path == pattern.rstrip("/"):
            return pattern
    return None


def decide(event: dict, policy: dict) -> str | None:
    """The reason to deny this tool call, or None to let it through."""
    tool = event.get("tool_name", "")
    data = event.get("tool_input") or {}
    root = Path(policy.get("root") or event.get("cwd") or ".")
    if tool == "Bash":
        subcommand = _denied_git(str(data.get("command", "")))
        if subcommand:
            return (f"ralph: `git {subcommand}` is the harness's job. Leave the changes in the "
                    f"working tree; the harness commits them after its checks pass.")
    if tool in EDIT_TOOLS:
        path = _relative(str(data.get("file_path") or data.get("notebook_path") or ""), root)
        if _matches(path, ALWAYS_PROTECTED + tuple(policy.get("protected", []))):
            return f"ralph: {path} is harness state. Report progress in your structured result instead."
        pattern = _matches(path, policy.get("frozen", []))
        if pattern:
            return f"ralph: {path} is frozen ({pattern}) and must not change."
        for lock in policy.get("locked", []):
            if _matches(path, [lock["path"]]):
                return (f"ralph: {path} is locked by {lock['holder']} ({lock['path']}); report it as a blocker "
                        "instead of changing it.")
    return None


def main(argv: list[str]) -> int:
    policy = json.loads(Path(argv[1]).read_text(encoding="utf-8")) if len(argv) > 1 else {}
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # not ours to judge; let Claude Code carry on
    reason = decide(event, policy)
    if reason:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
