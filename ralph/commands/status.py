"""`ralph status` — where a change request stands, and how the loop is doing."""

from ralph import ui
from ralph.commands.common import load_cr, load_project, require_units
from ralph.fsutil import read_text, write_text_atomic
from ralph.graph.schedule import next_unit, why_nothing_is_ready
from ralph.model.progress import render_progress_view
from ralph.model.unit import Status
from ralph.monitor.telemetry import read_records, unit_totals
from ralph.vcs.git import Git


def register(subparsers) -> None:
    p = subparsers.add_parser("status", help="progress, what runs next, and per-unit time, cost and context")
    p.add_argument("--cr", help="change request (default: the latest with units)")
    p.add_argument("--write", action="store_true", help="also write the generated PROGRESS.md view")
    p.set_defaults(handler=run)


SPEC_STUB = "What this change does and why"      # a line of templates/spec.md, gone once you write the spec


def before_the_build(project, cr) -> None:
    """Where a CR without units stands: write the spec, plan, or compile."""
    ui.header(f"status — {cr.name}", str(cr.dir))
    spec = read_text(cr.spec)
    if cr.legacy_progress.exists():
        ui.info(f"  This CR is from the previous loop. Run 'ralph migrate --cr {cr.name} --dry-run', "
                f"then 'ralph migrate --cr {cr.name}' on a branch.")
    elif not spec.strip() or SPEC_STUB in spec:
        ui.info(f"  Step 1 of 3 — write the spec: {cr.spec.relative_to(project.root)}")
        ui.info(f"  Number its sections (§1, §1.1, …), then: ralph start --mode plan --cr {cr.name}")
    elif not cr.plan.exists():
        ui.info(f"  Step 2 of 3 — plan it: ralph start --mode plan --cr {cr.name}")
        ui.info(f"  (Optional afterwards: ralph critique --cr {cr.name})")
    else:
        ui.info(f"  Step 3 of 3 — the plan exists; compile it into units: ralph graph compile --cr {cr.name}")
        ui.info(f"  Then build: ralph start --cr {cr.name}")


def run(args) -> int:
    project, _ = load_project()
    cr, store = load_cr(project, args.cr)
    if not store.exists():
        before_the_build(project, cr)
        return 0
    units = require_units(cr, store)
    counts = {s: sum(1 for u in units.values() if u.status is s) for s in Status}
    triage = [u for u in units.values() if u.triage]

    ui.header(f"status — {cr.name}", str(cr.dir))
    ui.info(f"  {ui.progress_bar(counts[Status.DONE], len(units))}")
    ui.info(f"  done {counts[Status.DONE]} · in progress {counts[Status.IN_PROGRESS]} · "
            f"to do {counts[Status.TODO]} · blocked {counts[Status.BLOCKED]}"
            + (f" · triage {len(triage)}" if triage else ""))

    upcoming = next_unit(units)
    ui.section("Next")
    if upcoming:
        ui.info(f"  {upcoming.id} {upcoming.title} (attempt {upcoming.attempts + 1}) — run: ralph start --cr {cr.name}")
    else:
        reasons = why_nothing_is_ready(units)
        ui.info("  Nothing is ready:" if reasons else "  Everything is done.")
        for reason in reasons:
            ui.info(f"    {reason}")

    blocked = [u for u in units.values() if u.status is Status.BLOCKED]
    if blocked:
        ui.section("Blocked")
        for unit in blocked:
            ui.info(f"  {unit.id} {unit.title}: {unit.blocker}")
        ui.info(f"  After fixing the cause: ralph unblock <ID>")
    if triage:
        ui.section("Found during other units (waiting for triage)")
        for unit in triage:
            ui.info(f"  {unit.id} (from {unit.discovered_from}) {unit.title}")
        ui.info("  Accept one with: ralph set <ID> --status todo   ·   drop it by deleting its file")

    totals = unit_totals(read_records(project.iterations_log), cr.name)
    if totals:
        ui.section("Per unit (from log/iterations.jsonl)")
        ui.info(f"  {'unit':<10} {'iters':>5} {'turns':>6} {'min':>6} {'$':>7} {'peak ctx':>9} {'compact':>7}")
        for unit_id, t in list(totals.items())[-10:]:
            ui.info(f"  {unit_id:<10} {t['iterations']:>5} {t['turns']:>6} {t['minutes']:>6.1f} "
                    f"{t['cost_usd']:>7.2f} {t['peak_context']:>9,} {t['compactions']:>7}")

    git = Git(project.root)
    if git.is_repo():
        ui.section("Recent commits")
        for line in git.run("log", "--oneline", "-5", check=False).stdout.splitlines():
            ui.info(f"  {line}")

    if args.write:
        write_text_atomic(cr.progress_view, render_progress_view(cr.name, list(units.values())))
        ui.ok(f"Wrote {cr.progress_view}")
    return 0
