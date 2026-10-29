"""The `ralph` command line.

Commands that exist in the harness run here. The plan-review and delivery
workflows (critique, refine-plan, retro, deploy) still run as the original
bash scripts until they're ported (P5), and `ralph legacy <command>` runs
any original command, e.g. the previous loop for a CR that isn't migrated.
"""

import argparse
import os
import subprocess
import sys

from ralph import __version__, ui
from ralph.commands import control, doctor, graph, migrate, scaffold, start, status, units, watch
from ralph.errors import RalphError
from ralph.prompts import PACKAGE_DIR

REPO_DIR = PACKAGE_DIR.parent
BASH_COMMANDS = {"critique", "refine-plan", "retro", "deploy"}
LEGACY_COMMANDS = {"init", "add-cr", "status", "start", "watch", *BASH_COMMANDS}
COMMAND_MODULES = (scaffold, start, status, graph, units, control, watch, migrate, doctor)

EPILOG = """\
typical workflow:
  ralph init                         set up the project, then write development/initial/spec/spec.md
  ralph start --mode plan --cr initial
  ralph graph                        review the units and their order
  ralph start --cr initial           one bounded, verified unit per iteration
  ralph watch                        (another terminal) follow it live
  ralph status                       progress, blocked units, per-unit time/cost/context

still run by the original bash scripts: critique, refine-plan, retro, deploy
the previous loop, for CRs that aren't migrated: ralph legacy start --cr N
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ralph", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
        description="A meta-harness for Claude Code: one bounded, verified unit of work per iteration.")
    parser.add_argument("--version", action="version", version=f"ralph {__version__}")
    subparsers = parser.add_subparsers(title="commands", metavar="<command>")
    for module in COMMAND_MODULES:
        module.register(subparsers)
    for name in sorted(BASH_COMMANDS):
        subparsers.add_parser(name, help="(runs the original bash script)", add_help=False)
    subparsers.add_parser("legacy", help="run an original bash command, e.g. 'ralph legacy start --cr 2'",
                          add_help=False)
    return parser


def run_bash(command: str, args: list[str]) -> int:
    script = REPO_DIR / "commands" / f"{command}.sh"
    if command not in LEGACY_COMMANDS or not script.exists():
        ui.error(f"There's no original command '{command}'.",
                 f"Original commands: {', '.join(sorted(LEGACY_COMMANDS))}")
        return 1
    return subprocess.call(["bash", str(script), *args], env={**os.environ, "RALPH_LEGACY": "1"})


def main(argv: list[str]) -> int:
    if argv and argv[0] in BASH_COMMANDS:
        return run_bash(argv[0], argv[1:])
    if argv and argv[0] == "legacy":
        if len(argv) < 2:
            ui.error("Say which command, e.g. 'ralph legacy start --cr 2'.")
            return 1
        return run_bash(argv[1], argv[2:])

    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        return 0
    try:
        return args.handler(args)
    except RalphError as problem:
        ui.error(problem.message, problem.hint)
        return 1
    except KeyboardInterrupt:
        ui.warn("Stopped.")
        return 130


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
