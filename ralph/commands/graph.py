"""`ralph graph` and `ralph ready` — the work graph."""

import json

from ralph import ui
from ralph.commands.common import load_cr, load_project, require_units
from ralph.errors import RalphError
from ralph.graph.complexity import analyze
from ralph.graph.render import mermaid, text_list
from ralph.graph.schedule import ready_units
from ralph.kernel.loop import Kernel, StartOptions
from ralph.kernel.modes import compile_and_commit
from ralph.model.plan import parse_plan
from ralph.model.unit import Status
from ralph.runner import compat
from ralph.runner.cli_backend import CliRunner


def register(subparsers) -> None:
    p = subparsers.add_parser("graph", help="show the units and their dependencies, or compile the plan into units")
    p.add_argument("action", nargs="?", choices=("show", "compile"), default="show")
    p.add_argument("--cr", help="change request (default: the latest with units or a plan)")
    p.add_argument("--mermaid", action="store_true", help="print a Mermaid diagram (renders on GitHub and in VS Code)")
    p.add_argument("--json", action="store_true", help="print the units as JSON")
    p.add_argument("--allow-large", action="store_true", help="compile: accept units the size check rejects")
    p.add_argument("--analyze", action="store_true",
                   help="compile: also have Claude score each open unit's complexity and propose splits "
                        "(one read-only call, a few cents)")
    p.set_defaults(handler=run_graph)

    r = subparsers.add_parser("ready", help="list the units that can start now")
    r.add_argument("--cr", help="change request")
    r.set_defaults(handler=run_ready)


def run_graph(args) -> int:
    project, config = load_project()
    cr, store = load_cr(project, args.cr)
    if args.action == "compile":
        if not cr.plan.exists():
            raise RalphError(f"There's no plan at {cr.plan}.", f"Write one with 'ralph start --mode plan --cr {cr.name}'.")
        kernel = Kernel(project, config, CliRunner(), StartOptions(cr=cr.name))
        status = compile_and_commit(kernel, f"ralph: compile the {cr.name} plan", allow_large=args.allow_large)
        return analyze_complexity(kernel) if status == 0 and args.analyze else status
    units = require_units(cr, store)
    if args.json:
        print(json.dumps([u.to_json() for u in units.values()], indent=2, ensure_ascii=False))
    elif args.mermaid:
        print(mermaid(units), end="")
    else:
        ui.header(f"graph — {cr.name}", "[x] done · [~] in progress · [ ] to do · [!] blocked")
        ui.info(text_list(units))
    return 0


def analyze_complexity(kernel: Kernel) -> int:
    """Score the open units from 1 to 5; list splits for the 4s and 5s. Advisory: it changes nothing."""
    compat.find_claude()
    units = {k: u for k, u in kernel.store.load().items() if u.status is not Status.DONE}
    if not units:
        return 0
    ui.info(f"\n  Scoring the complexity of {len(units)} open unit(s)…")
    scores = analyze(kernel.runner, kernel.root, parse_plan(kernel.cr.plan), units,
                     kernel.run_dir / "complexity", model=kernel.config.runner.model_build)
    if not scores:
        ui.warn(f"Claude returned no scores; see {kernel.run_dir / 'complexity' / 'stream.jsonl'}.")
        return 0
    for score in sorted(scores, key=lambda s: -s.score):
        ui.info(f"  {score.unit_id:<10} {'●' * score.score}{'○' * (5 - score.score)} {score.score}  {score.reason}")
        for part in score.split if score.too_complex else []:
            ui.info(f"               split: {part}")
    hard = [s.unit_id for s in scores if s.too_complex]
    if hard:
        ui.warn(f"{', '.join(hard)} may not fit one session. Split them in the plan and compile again, "
                "or leave them and let the size caps decide.")
    return 0


def run_ready(args) -> int:
    project, _ = load_project()
    cr, store = load_cr(project, args.cr)
    ready = ready_units(require_units(cr, store))
    if not ready:
        ui.info("Nothing is ready. 'ralph status' says why.")
    for unit in ready:
        ui.info(f"  {unit.id:<10} {unit.priority:<3} {unit.title}")
    return 0
