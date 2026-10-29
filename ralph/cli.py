"""The `ralph` command line: one module per group of commands, in ralph/commands/."""

import argparse
import sys

from ralph import __version__, ui
from ralph.commands import (control, doctor, graph, memory, migrate, scaffold, skills, start, status, team, units,
                            watch, workflows)
from ralph.errors import RalphError

COMMAND_MODULES = (scaffold, start, status, graph, units, control, watch, workflows, team, memory, skills,
                   migrate, doctor)

EPILOG = """\
typical workflow:
  ralph init                         set up the project, then write development/initial/spec/spec.md
  ralph skills init --stack "…"      (optional) draft starter skills for your stack, then review them
  ralph start --mode plan --cr initial
  ralph critique                     (optional) 8 experts review the plan; ralph refine-plan applies fixes
  ralph graph                        review the units and their order
  ralph start --cr initial           one bounded, verified unit per iteration
  ralph watch                        (another terminal) follow it live; --serve for a browser view
  ralph status                       progress, blocked units, per-unit time/cost/context
  ralph memory review                now and then: promote the lessons the agent reported
  ralph retro / ralph deploy …       when the CR is built: look back, then prepare the deploy

with a team (manual; the loop itself never fetches or pushes):
  ralph sync                         fetch the shared branch, see what affects your units, rebase if you agree
  ralph sync push                    review what would be published, then push it
  ralph changes / team / impact ID   what changed and who it affects, as of your last sync

a project from the previous bash loop: ralph migrate --cr N --dry-run, then ralph migrate --cr N
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ralph", formatter_class=argparse.RawDescriptionHelpFormatter, epilog=EPILOG,
        description="A meta-harness for Claude Code: one bounded, verified unit of work per iteration.")
    parser.add_argument("--version", action="version", version=f"ralph {__version__}")
    subparsers = parser.add_subparsers(title="commands", metavar="<command>")
    for module in COMMAND_MODULES:
        module.register(subparsers)
    return parser


def main(argv: list[str]) -> int:
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


def entry() -> None:
    """The `ralph` console script, when installed with pip or pipx."""
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    entry()
