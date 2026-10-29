"""`ralph doctor` — check that this machine and project are ready for the loop.

`--live` also makes one tiny real call (Haiku, about a cent) to confirm the
hermetic flags hold and structured results arrive with this Claude Code.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from ralph import ui
from ralph.commands.common import load_project
from ralph.errors import RalphError
from ralph.runner import compat
from ralph.runner.base import AgentCall
from ralph.runner.cli_backend import CliRunner
from ralph.runner.settings import agent_env, project_mcp_config
from ralph.vcs.git import Git


def register(subparsers) -> None:
    p = subparsers.add_parser("doctor", help="check Claude Code, git and the project config")
    p.add_argument("--live", action="store_true", help="also make one tiny real Claude call (about a cent)")
    p.set_defaults(handler=run)


def run(args) -> int:
    problems = 0

    def check(ok: bool, good: str, bad: str, hint: str = "") -> None:
        nonlocal problems
        if ok:
            ui.ok(good)
        else:
            problems += 1
            ui.error(bad, hint)

    ui.header("doctor")
    check(sys.version_info >= (3, 11), f"Python {sys.version.split()[0]}", "Python 3.11 or newer is needed")
    try:
        compat.find_claude()
        version = compat.installed_version()
        check(bool(version), f"Claude Code {version}", "Couldn't read the Claude Code version")
        missing = compat.missing_flags()
        check(not missing, "Claude Code accepts every flag ralph uses",
              f"Claude Code's --help doesn't list: {', '.join(missing)}",
              "A newer or older version may have changed them; see docs/meta-harness-plan.md §12.6.")
    except RalphError as problem:
        check(False, "", problem.message, problem.hint)
        version = ""

    try:
        project, config = load_project()
    except RalphError as problem:
        check(False, "", problem.message, problem.hint)
        return 1
    pinned = config.runner.claude_version
    if pinned:
        check(pinned == version, f"Matches the pinned version {pinned}",
              f"Installed {version or '?'}, but runner.claude_version pins {pinned}")
    else:
        ui.info("  ·  No Claude Code version pinned (runner.claude_version); updates won't be noticed.")

    git = Git(project.root)
    check(git.is_repo(), f"git repository at {project.root}", f"{project.root} isn't a git repository")
    if git.is_repo():
        check(git.user_configured(), "git user is set", "git user.email isn't set, so the harness can't commit",
              "git config user.name 'Your Name' && git config user.email you@example.com")
    check(bool(config.verify.gates), f"Gates: {', '.join(f'{k} = {v}' for k, v in config.verify.gates.items())}",
          "No gates in .ralph/config.toml, so only the reviewer checks the agent's work",
          "Add a [verify.gates] table with your test commands.")
    with tempfile.TemporaryDirectory() as tmp:
        mcp = project_mcp_config(project.root, Path(tmp))
        servers = list(json.loads(mcp.read_text()).get("mcpServers", {}))
        ui.info(f"  ·  Loop runs get these MCP servers: {', '.join(servers) or 'none'} ({mcp.name})")

    if args.live and not problems:
        problems += _live_check(project.root, config.runner.compact_window)
    ui.info("")
    ui.info("  All good." if not problems else f"  {problems} problem(s) to fix.")
    return 0 if not problems else 1


def _live_check(root: Path, compact_window: int) -> int:
    """One real call in a scratch repo, with the same hermetic flags as the loop."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        schema = {"type": "object", "required": ["unit", "status"],
                  "properties": {"unit": {"type": "string"}, "status": {"type": "string"}}}
        call = AgentCall(prompt="Report unit U001 with status done.", cwd=work, model="haiku",
                         log_path=work / "stream.jsonl", max_turns=3, max_budget_usd=0.25, timeout_seconds=180,
                         json_schema=schema, tools=[], mcp_config=project_mcp_config(work, work),
                         env=agent_env(work, compact_window, hermetic=True))
        result = CliRunner().run(call)
        init = result.stats.init or {}
        personal = [p.get("name") for p in init.get("plugins") or [] if isinstance(p, dict) and p.get("path") != "builtin"]
        failures = 0
        for ok, good, bad in (
            (result.structured == {"unit": "U001", "status": "done"} or bool(result.structured),
             "Structured results arrive", f"No structured result ({result.subtype or 'no result'})"),
            (not init.get("mcp_servers"), "No MCP servers leak into loop runs",
             f"MCP servers loaded: {[s.get('name') for s in init.get('mcp_servers')]}"),
            (not personal, "No personal plugins load in loop runs", f"Personal plugins loaded: {personal}"),
        ):
            if ok:
                ui.ok(good)
            else:
                ui.error(bad)
                failures += 1
        ui.info(f"  ·  Live check cost ${result.result.get('total_cost_usd') or 0:.4f}")
        return failures
