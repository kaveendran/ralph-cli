"""How much Claude may do without asking: `runner.permissions` (docs/meta-harness-plan.md §12.5).

  bypass     Skip permission prompts; the guardrail hooks stop accidents. For attended
             runs on your own machine. The hooks prevent mistakes, not misuse.
  container  The same, but it refuses to start outside a container. For unattended runs.
  strict     `--permission-mode dontAsk`: only an allowlist runs (the file tools, the
             gate commands, and `runner.allow`). Anything else is denied, never prompted.
  sandbox    `acceptEdits` inside Claude Code's OS sandbox (Seatbelt on macOS, bubblewrap
             on Linux and WSL2): commands can write only to the project and reach only
             `runner.sandbox_domains`. It fails to start rather than run unsandboxed.

The guardrail hooks run in every mode. Rule syntax and modes:
code.claude.com/docs/en/permissions, /permission-modes, /sandboxing.
"""

import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ralph.config import RunnerConfig
from ralph.errors import RalphError

MODES = ("bypass", "container", "strict", "sandbox")
# What a unit needs besides shell commands; read-only shell commands (ls, cat, git log, …)
# never need approval. The subagent tool is `Task` in Claude Code 2.1.3xx and `Agent` in
# newer docs: both are allowed.
STRICT_TOOLS = ["Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Glob", "Grep", "Task", "Agent", "TodoWrite",
                "Skill"]
ONE_AT_A_TIME = ("Run each command on its own, exactly as written: no `;`, `&&`, pipes or redirections around it, "
                 "since a compound command needs an approval no one is there to give.")
NOTES = {
    "strict": ("This run uses an allowlist: shell commands other than read-only ones and the checks listed in your "
               f"message are denied, not asked about. {ONE_AT_A_TIME} If you need a command that isn't allowed, "
               "report it as a blocker."),
    "sandbox": ("Shell commands run in a sandbox: they can write only inside the project and reach only the allowed "
                f"network hosts. {ONE_AT_A_TIME} If a command fails because of the sandbox, report it as a blocker "
                "rather than working around it."),
}


@dataclass
class Posture:
    skip_permissions: bool
    mode: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    sandbox: dict | None = None


def in_container() -> bool:
    return (Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()
            or os.environ.get("RALPH_CONTAINER") == "1")


def check(config: RunnerConfig) -> None:
    """Refuse a mode that can't work here, before any unit runs."""
    if config.permissions not in MODES:
        raise RalphError(f"runner.permissions = '{config.permissions}' isn't a mode.",
                         f"Use one of: {', '.join(MODES)}.")
    if config.permissions == "container" and not in_container():
        raise RalphError("runner.permissions = 'container', but this doesn't look like a container.",
                         "Run ralph inside one (or set RALPH_CONTAINER=1 there), or use 'sandbox' or 'strict'.")
    if config.permissions == "sandbox":
        if sys.platform == "win32":
            raise RalphError("Claude Code's sandbox doesn't run on native Windows.", "Run ralph under WSL2.")
        missing = [tool for tool in ("bwrap", "socat") if sys.platform.startswith("linux") and not shutil.which(tool)]
        if missing:
            raise RalphError(f"The sandbox needs {' and '.join(missing)} on Linux.",
                             "Install bubblewrap and socat (e.g. apt install bubblewrap socat).")


def note(config: RunnerConfig) -> str:
    """What the agent needs to know about its permissions, added to the loop contract."""
    return NOTES.get(config.permissions, "")


def command_rules(commands: list[str]) -> list[str]:
    """Allow rules for exact commands, with or without extra arguments."""
    rules = []
    for command in dict.fromkeys(c.strip() for c in commands if c.strip()):
        rules += [f"Bash({command})", f"Bash({command} *)"]
    return rules


def posture(config: RunnerConfig, commands: list[str]) -> Posture:
    """The flags and settings for a build run. `commands`: the checks the agent should be able to run."""
    if config.permissions in ("bypass", "container"):
        return Posture(skip_permissions=True)
    if config.permissions == "strict":
        return Posture(False, "dontAsk", STRICT_TOOLS + command_rules(commands) + list(config.allow))
    sandbox = {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False,
               "excludedCommands": list(config.sandbox_excluded),
               "network": {"allowedDomains": list(config.sandbox_domains)}}
    # Commands left outside the sandbox would prompt, which a headless run can't answer: allow them.
    return Posture(False, "acceptEdits", [f"Bash({c})" for c in config.sandbox_excluded], sandbox)
