"""`ralph graph` and `ralph ready` — the work graph."""

import json

from ralph import ui
from ralph.commands.common import load_cr, load_project, require_units
from ralph.errors import RalphError
from ralph.graph.render import mermaid, text_list
from ralph.graph.schedule import ready_units
from ralph.kernel.loop import Kernel, StartOptions
from ralph.kernel.modes import compile_and_commit
from ralph.runner.cli_backend import CliRunner


def register(subparsers) -> None:
    p = subparsers.add_parser("graph", help="show the units and their dependencies, or compile the plan into units")
    p.add_argument("action", nargs="?", choices=("show", "compile"), default="show")
    p.add_argument("--cr", help="change request (default: the latest with units or a plan)")
    p.add_argument("--mermaid", action="store_true", help="print a Mermaid diagram (renders on GitHub and in VS Code)")
    p.add_argument("--json", action="store_true", help="print the units as JSON")
    p.add_argument("--allow-large", action="store_true", help="compile: accept units the size check rejects")
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
        return compile_and_commit(kernel, f"ralph: compile the {cr.name} plan", allow_large=args.allow_large)
    units = require_units(cr, store)
    if args.json:
        print(json.dumps([u.to_json() for u in units.values()], indent=2, ensure_ascii=False))
    elif args.mermaid:
        print(mermaid(units), end="")
    else:
        ui.header(f"graph — {cr.name}", "[x] done · [~] in progress · [ ] to do · [!] blocked")
        ui.info(text_list(units))
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
