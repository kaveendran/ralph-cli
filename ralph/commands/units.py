"""`ralph unblock` and `ralph set` — change a unit's state by hand.

Unit files are only ever changed through the harness or these commands,
which also commit the change so git always shows the current state.
"""

from ralph import ui
from ralph.commands.common import commit_state, find_unit, load_cr, load_project, require_units
from ralph.model.unit import Status


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
