"""`ralph start` — run the loop (build mode), write the plan (plan mode),
do one task outside the plan, or show what the next iteration would send."""

from pathlib import Path

from ralph.commands.common import load_project, require_units
from ralph.errors import RalphError
from ralph.kernel.loop import Kernel, StartOptions
from ralph.kernel.modes import dry_run, run_plan, run_task
from ralph.paths import normalise_cr, resolve_cr
from ralph.runner.cli_backend import CliRunner


def register(subparsers) -> None:
    p = subparsers.add_parser("start", help="run the loop: one bounded, verified unit per iteration")
    p.add_argument("--cr", help="change request: 5, cr5 or initial (default: the latest with units)")
    p.add_argument("--mode", choices=("build", "plan"), default="build",
                   help="build: implement units (default); plan: write the plan from the spec")
    p.add_argument("--task", metavar="TEXT", help="do one task outside the plan, e.g. a bug fix")
    p.add_argument("--task-file", metavar="PATH", help="like --task, reading the task from a file")
    p.add_argument("--unit", action="append", default=[], metavar="ID", help="only run these units (repeatable)")
    p.add_argument("--max", type=int, default=0, metavar="N", help="stop after N iterations (default: no limit)")
    p.add_argument("--model", default="", help="Claude model (default: sonnet for build, opus for plan)")
    p.add_argument("--max-turns", type=int, metavar="N", help="turn cap per iteration")
    p.add_argument("--max-budget", type=float, metavar="USD", help="spend cap per iteration")
    p.add_argument("--review", choices=("off", "p0-and-large", "all"), help="which units get the per-unit review")
    p.add_argument("--dry-run", action="store_true", help="show the next unit's context pack and command; run nothing")
    p.add_argument("--allow-main", action="store_true", help="allow running on the default branch")
    p.add_argument("--skip-baseline", action="store_true", help="don't run the gates once before the first unit")
    p.add_argument("--gates-advisory", action="store_true", help="continue even if the baseline gates fail")
    p.add_argument("--allow-version-drift", action="store_true",
                   help="run even if Claude Code differs from runner.claude_version")
    p.add_argument("--type", default="feature", help="implementation type for a new plan (plan mode)")
    p.set_defaults(handler=run)


def run(args) -> int:
    project, config = load_project()
    if args.mode == "plan":
        if not args.cr:
            raise RalphError("Plan mode needs --cr, so the plan goes to the right change request.")
        cr_name = normalise_cr(args.cr)
    else:
        cr_name = resolve_cr(project, args.cr)

    options = StartOptions(cr=cr_name, max_iterations=args.max, model=args.model, max_turns=args.max_turns,
                           max_budget_usd=args.max_budget, only_units=args.unit, allow_main=args.allow_main,
                           skip_baseline=args.skip_baseline, gates_advisory=args.gates_advisory,
                           allow_version_drift=args.allow_version_drift, review=args.review)
    kernel = Kernel(project, config, CliRunner(), options)

    if args.mode == "plan":
        return run_plan(kernel, args.type)
    if args.task or args.task_file:
        text = args.task or Path(args.task_file).read_text(encoding="utf-8")
        title = args.task or Path(args.task_file).stem
        return run_task(kernel, title.strip().splitlines()[0][:80], text)
    require_units(kernel.cr, kernel.store)
    if args.dry_run:
        return dry_run(kernel)
    return kernel.run()
