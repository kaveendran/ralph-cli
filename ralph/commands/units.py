"""`ralph unblock`, `ralph set` and `ralph add-unit` — change units by hand.

Unit files are only ever changed through the harness or these commands,
which also commit the change so git always shows the current state.
"""

from ralph import ui
from ralph.commands.common import commit_state, find_unit, load_cr, load_project, require_units
from ralph.errors import RalphError
from ralph.graph.compile import added_unit_id
from ralph.model.unit import Status, Unit
from ralph.vcs.git import Git

MAX_KEY_FILES = 8           # the same size check the plan compiler applies


def register(subparsers) -> None:
    u = subparsers.add_parser("unblock", help="make a blocked unit runnable again (resets its attempts)")
    u.add_argument("unit", help="unit ID, e.g. U013")
    u.add_argument("--cr", help="change request")
    u.set_defaults(handler=run_unblock)

    s = subparsers.add_parser("set", help="change a unit by hand")
    s.add_argument("unit", help="unit ID")
    s.add_argument("--cr", help="change request")
    s.add_argument("--status", choices=[s.value for s in Status], help="new status (todo also accepts a triage unit)")
    s.add_argument("--reason", default="", help="why (recorded as the blocker for --status blocked)")
    s.add_argument("--allow-large", action="store_true", help="waive the size check for this unit")
    s.set_defaults(handler=run_set)

    a = subparsers.add_parser("add-unit", help="add a unit that isn't in the plan (it runs like any other)")
    a.add_argument("title", help="what the unit does, in a few words")
    a.add_argument("--cr", help="change request")
    a.add_argument("--description", default="", help="what to build and how to check it (the agent's brief)")
    a.add_argument("--depends-on", action="append", default=[], metavar="ID", help="a unit it needs (repeatable)")
    a.add_argument("--key-file", action="append", default=[], metavar="PATH", help="a file it will change (repeatable)")
    a.add_argument("--type", default="", help="unit type, e.g. api (picks its gates)")
    a.add_argument("--priority", choices=("P0", "P1", "P2", "P3"), default="P2")
    a.add_argument("--allow-large", action="store_true", help=f"accept more than {MAX_KEY_FILES} key files")
    a.set_defaults(handler=run_add_unit)


def run_unblock(args) -> int:
    project, _ = load_project()
    cr, store = load_cr(project, args.cr)
    unit = find_unit(require_units(cr, store), args.unit)
    unit.status, unit.blocker, unit.attempts, unit.last_review_gaps = Status.TODO, "", 0, []
    store.save(unit)
    store.append_note(unit.id, "Unblocked by hand", "Attempts reset to 0.")
    commit_state(project, store, [unit], f"ralph: unblock {unit.id}")
    ui.ok(f"{unit.id} is runnable again (attempts reset).")
    return 0


def run_set(args) -> int:
    project, _ = load_project()
    cr, store = load_cr(project, args.cr)
    unit = find_unit(require_units(cr, store), args.unit)
    changes = []
    if args.status:
        unit.status = Status(args.status)
        if unit.status is Status.TODO and unit.triage:
            unit.triage = False
            changes.append("accepted from triage")
        unit.blocker = args.reason if unit.status is Status.BLOCKED else ""
        changes.append(f"status {unit.status.value}")
    if args.allow_large:
        unit.allow_large = True
        changes.append("size check waived")
    if not changes:
        ui.info("Nothing to change. See 'ralph set --help'.")
        return 0
    store.save(unit)
    store.append_note(unit.id, "Changed by hand", "; ".join(changes) + (f" — {args.reason}" if args.reason else ""))
    commit_state(project, store, [unit], f"ralph: {unit.id} {', '.join(changes)}")
    ui.ok(f"{unit.id}: {', '.join(changes)}.")
    return 0


def run_add_unit(args) -> int:
    project, _ = load_project()
    cr, store = load_cr(project, args.cr)
    units = require_units(cr, store)
    missing = [d for d in args.depends_on if d not in units]
    if missing:
        raise RalphError(f"{', '.join(missing)} isn't a unit of {cr.name}.", "List them with 'ralph graph'.")
    if len(args.key_file) > MAX_KEY_FILES and not args.allow_large:
        raise RalphError(f"{len(args.key_file)} key files is more than one session handles well ({MAX_KEY_FILES}).",
                         "Split it into several units, or pass --allow-large.")
    author = Git(project.root).run("config", "user.email", check=False).stdout.strip()
    unit_id = added_unit_id(units, args.title, author)
    order = max([units[d].order for d in args.depends_on] or [max((u.order for u in units.values()), default=0)]) + 1
    unit = Unit(unit_id, args.title.strip(), cr.name, type=args.type, priority=args.priority, order=order,
                depends_on=args.depends_on, key_files=args.key_file, allow_large=args.allow_large,
                description=args.description.strip() or args.title.strip())
    store.save(unit)
    store.append_note(unit.id, "Added by hand", unit.description)
    commit_state(project, store, [unit], f"ralph: add {unit.id} {unit.title}")
    ui.ok(f"Added {unit.id} {unit.title}" + (f" (after {', '.join(args.depends_on)})" if args.depends_on else "") + ".")
    if not args.description:
        ui.info("  Tip: --description gives the agent a proper brief; the title alone is thin.")
    return 0
