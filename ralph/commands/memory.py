"""`ralph memory lint | review | decay` — keep project memory small and useful.

- lint: the size budgets for CLAUDE.md, AGENTS.md, rules, skills and notes.
- review: walk the lessons the agent reported, most-recurring first, and
  promote each one (with your approval) to a rule, CLAUDE.md or a skill.
  What it writes is left uncommitted, so you can reword it before you commit.
- decay: summarise the notes of units finished long ago and archive the
  full text, and move reviewed lessons out of the inbox.
- export: everything above as one graph, for a graph store (memory/graph.py).
"""

import json
from pathlib import Path

from ralph import ui
from ralph.commands.common import load_cr, load_project, require_units
from ralph.fsutil import write_text_atomic
from ralph.memory.decay import archive_reviewed, decay
from ralph.memory.graph import build
from ralph.memory.learnings import group, read_inbox, write_inbox
from ralph.memory.lint import lint_memory
from ralph.memory.promote import apply, propose
from ralph.vcs import commit as vcs
from ralph.vcs.git import Git


def register(subparsers) -> None:
    p = subparsers.add_parser("memory", help="keep project memory small: lint budgets, review lessons, decay old notes")
    actions = p.add_subparsers(title="actions", metavar="<action>", required=True)

    lint = actions.add_parser("lint", help="check CLAUDE.md, rules, skills and notes against their size budgets")
    lint.add_argument("--strict", action="store_true", help="exit 1 if anything is over budget (for CI)")
    lint.set_defaults(handler=run_lint)

    review = actions.add_parser("review", help="promote the agent's lessons to rules, CLAUDE.md or skills")
    review.add_argument("--list", action="store_true", help="only list the lessons waiting for review")
    review.set_defaults(handler=run_review)

    d = actions.add_parser("decay", help="summarise the notes of units finished long ago; archive reviewed lessons")
    d.add_argument("--cr", help="change request (default: the latest)")
    d.add_argument("--keep", type=int, default=5, help="leave the notes of the N most recently done units (default 5)")
    d.add_argument("--dry-run", action="store_true", help="show what would change")
    d.set_defaults(handler=run_decay)

    e = actions.add_parser("export", help="write units, lessons and changes as one graph, for a graph store")
    e.add_argument("--out", help="write to this file (default: print it)")
    e.set_defaults(handler=run_export)


def _shown(path: Path, root: Path) -> str:
    return str(path.relative_to(root)) if path.is_absolute() and path.is_relative_to(root) else str(path)


def run_lint(args) -> int:
    project, config = load_project()
    units_dirs = [project.cr(name).units_dir for name in project.cr_names()]
    findings = lint_memory(project.root, config.memory, units_dirs, project.learnings_inbox)
    if not findings:
        ui.ok("Memory is within its budgets.")
        return 0
    for finding in findings:
        ui.warn(f"{_shown(finding.path, project.root)}: {finding.message}")
    return 1 if args.strict else 0


def run_review(args) -> int:
    project, _ = load_project()
    items = read_inbox(project.learnings_inbox)
    groups = [g for g in group(items) if g.status == "new"]
    if not groups:
        ui.info("No lessons are waiting for review.")
        return 0
    ui.header("memory review", f"{len(groups)} lesson(s), most-recurring first")
    changed: set[Path] = set()
    for number, lesson in enumerate(groups, start=1):
        proposal = propose(lesson, project.root)
        target = _shown(proposal.path, project.root)
        ui.info(f"\n  {number}/{len(groups)}  {lesson.rule}")
        ui.info(f"        from {', '.join(lesson.units) or '?'}" + (f" · paths {', '.join(lesson.paths)}" if lesson.paths else ""))
        if lesson.items[0].evidence:
            ui.info(f"        evidence: {lesson.items[0].evidence}")
        ui.info(f"        → {target} ({proposal.why})")
        if args.list:
            continue
        for line in proposal.text.splitlines():
            ui.info(f"          + {line}")
        answer = ui.ask("  Apply, skip, dismiss or quit? [a/s/d/q]", "s").lower()[:1]
        if answer == "q":
            break
        if answer == "a":
            apply(proposal, paths_for_rule=lesson.paths)
            changed.add(proposal.path)
            for item in lesson.items:
                item.status, item.promoted_to = "promoted", target
        elif answer == "d":
            for item in lesson.items:
                item.status = "dismissed"
        write_inbox(project.learnings_inbox, items)    # after each answer, so quitting keeps them

    if changed:
        ui.ok(f"Updated {', '.join(sorted(_shown(p, project.root) for p in changed))}.")
        ui.info("  Review the wording with 'git diff', then commit it with the inbox.")
    return 0


def run_decay(args) -> int:
    project, _ = load_project()
    cr, store = load_cr(project, args.cr)
    units = require_units(cr, store)
    decayed = decay(units, store, cr.archive_dir, project.root, keep_recent=args.keep, dry_run=args.dry_run)
    archived = archive_reviewed(project.learnings_inbox, dry_run=args.dry_run)
    verb = "Would summarise" if args.dry_run else "Summarised"
    if decayed:
        ui.ok(f"{verb} the notes of {', '.join(decayed)} (full text in {_shown(cr.archive_dir, project.root)}/notes/).")
    if archived:
        ui.ok(f"{'Would move' if args.dry_run else 'Moved'} {archived} reviewed lesson(s) out of the inbox.")
    if not (decayed or archived):
        ui.info("Nothing to decay.")
        return 0
    if args.dry_run:
        return 0
    git = Git(project.root)
    if git.is_repo():
        paths = [store.notes_path(u) for u in decayed] + [cr.archive_dir / "notes" / f"{u}.notes.md" for u in decayed]
        if archived:
            paths += [project.learnings_inbox, project.learnings_inbox.with_name("archive.jsonl")]
        vcs.commit_paths(git, [_shown(p, project.root) for p in paths if p.exists()],
                         f"ralph: memory decay for {cr.name}")
    return 0


def run_export(args) -> int:
    project, _ = load_project()
    graph = build(project)
    text = json.dumps(graph, indent=2, ensure_ascii=False) + "\n"
    if not args.out:
        print(text, end="")
        return 0
    write_text_atomic(Path(args.out), text)
    ui.ok(f"Wrote {len(graph['nodes'])} nodes and {len(graph['edges'])} edges to {args.out}.")
    return 0
