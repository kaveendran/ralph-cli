"""`ralph sync`, `ralph sync push`, `ralph changes`, `ralph impact`, `ralph team`.

Sync is manual (docs/meta-harness-plan.md §11): `ralph sync` fetches the
shared branch and shows what teammates landed and how it affects your open
units, then rebases only if you say yes; `ralph sync push` shows what it
would publish and pushes only after you confirm. The other three commands
read what's already on your machine.
"""

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from ralph import ui
from ralph.commands.common import find_unit, load_cr, load_project, require_units
from ralph.errors import RalphError
from ralph.kernel.lock import RunLock
from ralph.model.plan import parse_plan
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.paths import ProjectPaths
from ralph.commands.claims import release_claims
from ralph.team import remote
from ralph.team.claims import read_claims, read_locks
from ralph.team.impact import age, assess, team_view, touches
from ralph.team.ledger import LEDGER_DIR, read_local, read_pending
from ralph.team.shared import Shared, ahead_behind, has_ref, last_fetch, local_ref, shared_branch
from ralph.vcs.git import Git


def register(subparsers) -> None:
    s = subparsers.add_parser("sync", help="fetch teammates' work and see how it affects yours; 'sync push' publishes")
    s.add_argument("action", nargs="?", choices=("push",), help="push: publish your commits to the shared branch")
    s.add_argument("--yes", action="store_true", help="don't ask: rebase (sync) or push (sync push)")
    s.add_argument("--no-rebase", action="store_true", help="sync: only fetch and report")
    s.set_defaults(handler=run_sync)

    c = subparsers.add_parser("changes", help="what teammates and you changed recently (the ledger), as of your last sync")
    c.add_argument("--days", type=int, default=7, help="how far back (default 7)")
    c.set_defaults(handler=run_changes)

    i = subparsers.add_parser("impact", help="teammates' changes that affect a unit, or the units a finished one affects")
    i.add_argument("unit", help="unit ID")
    i.add_argument("--cr", help="change request")
    i.set_defaults(handler=run_impact)

    t = subparsers.add_parser("team", help="who landed what recently, and the files more than one of you changed")
    t.add_argument("--days", type=int, default=14, help="how far back (default 14)")
    t.set_defaults(handler=run_team)


def _require_shared(git: Git, config) -> Shared:
    shared = shared_branch(git, config.team)
    if shared is None:
        raise RalphError("I don't know which branch you share with your team.",
                         "Set an upstream (git branch --set-upstream-to origin/<shared>) or set "
                         "[team] branch = \"<shared>\" in .ralph/config.toml.")
    return shared


def _email(git: Git) -> str:
    return git.run("config", "user.email", check=False).stdout.strip()


def _all_units(project: ProjectPaths) -> list[tuple[Unit, str]]:
    """(unit, plan text) for every unit, across CRs."""
    found = []
    for name in project.cr_names():
        cr = project.cr(name)
        store = UnitStore(cr.units_dir)
        if not store.exists():
            continue
        plan = parse_plan(cr.plan) if cr.plan.exists() else None
        for unit in store.load().values():
            plan_unit = plan.unit(unit.id) if plan else None
            found.append((unit, plan_unit.section if plan_unit else unit.description))
    return found


def _open_units(project: ProjectPaths) -> list[tuple[Unit, str]]:
    """(unit, plan text) for every unit not done yet, across CRs."""
    return [(u, text) for u, text in _all_units(project) if u.status is not Status.DONE and not u.triage]


def _entry_line(entry, me: str = "") -> str:
    who = "you" if me and entry.email.lower() == me.lower() else (entry.dev or entry.email)
    breaking = f" · BREAKING {', '.join(i['name'] for i in entry.breaking[:3])}" if entry.breaking else ""
    pending = " · not pulled yet" if entry.pending else ""
    return (f"  {entry.at[:16].replace('T', ' ')}  {who:<14} {entry.cr}/{entry.unit:<6} {entry.title[:48]:<48} "
            f"{len(entry.files)} files{breaking}{pending}")


# ── sync ─────────────────────────────────────────────────────────────────────

def run_sync(args) -> int:
    project, config = load_project()
    git = Git(project.root)
    shared = _require_shared(git, config)
    return push(project, git, config, shared, args) if args.action == "push" else pull(project, git, config, shared, args)


def pull(project: ProjectPaths, git: Git, config, shared: Shared, args) -> int:
    ui.header("sync", f"fetching {shared.name} and the team's claims and locks (nothing is pushed)")
    remote.fetch(git, shared)
    remote.fetch_team_refs(git, shared.remote, config.team.refs)
    view = team_view(git, config.team, _email(git))
    ahead, behind = ahead_behind(git, view.ref)
    incoming = [e for e in view.entries if e.pending]
    ui.info(f"  You're {ahead} commit(s) ahead and {behind} behind {shared.name}.")
    if incoming:
        ui.info(f"\n  {len(incoming)} unit(s) teammates landed that you haven't pulled:")
        for entry in incoming:
            ui.info(_entry_line(entry))

    affected = 0
    for unit, text in _open_units(project):
        report = assess(view, unit, text, project.root)
        for impact in report.duplicates + report.relevant:
            if affected == 0:
                ui.info("\n  How that affects your open units:")
            affected += 1
            ui.info(f"    {unit.id:<8} {impact.line}")
    if view.conflicts:
        ui.warn(f"Merging {shared.name} would conflict in: {', '.join(view.conflicts[:8])}")

    if behind == 0:
        ui.ok("Up to date with the shared branch.")
        return 0
    if args.no_rebase:
        ui.info(f"\n  Not rebasing (--no-rebase). The loop now knows about these changes; rebase with "
                f"'ralph sync' when you're ready.")
        return 0
    return _rebase(project, git, shared, args.yes)


def _rebase(project: ProjectPaths, git: Git, shared: Shared, yes: bool) -> int:
    running = RunLock(project.lock).holder()
    if running:
        ui.warn(f"A loop is running (pid {running['pid']}), so I won't rebase under it. "
                "Pause it ('ralph pause'), or sync again after it stops.")
        return 1
    tracked_changes = [p for p, code in git.status().items() if code != "??"]
    if tracked_changes:
        ui.warn(f"You have uncommitted changes ({', '.join(tracked_changes[:4])}"
                f"{'…' if len(tracked_changes) > 4 else ''}), so I won't rebase. Commit or stash them first.")
        return 1
    if not yes and not ui.confirm(f"\n  Rebase your branch onto {shared.name}?", default=False):
        ui.info("  Not rebased. The loop already takes these changes into account.")
        return 0
    result = git.run("rebase", shared.ref, check=False)
    if result.returncode != 0:
        conflicted = git.run("diff", "--name-only", "--diff-filter=U", check=False).stdout.split()
        ui.error(f"The rebase stopped on conflicts in: {', '.join(conflicted) or '(see git status)'}",
                 "Resolve them and run 'git rebase --continue', or 'git rebase --abort' to undo.")
        return 1
    ui.ok(f"Rebased onto {shared.name}.")
    return 0


def push(project: ProjectPaths, git: Git, config, shared: Shared, args) -> int:
    ref = shared.ref if has_ref(git, shared.ref) else ""     # "" when the shared branch was never fetched
    if ref and git.run("merge-base", "--is-ancestor", ref, "HEAD", check=False).returncode != 0:
        ui.error(f"Your branch is behind {shared.name} (as of your last fetch), so pushing would be rejected.",
                 "Run 'ralph sync' first.")
        return 1
    span = f"{ref}..HEAD" if ref else "HEAD"
    commits = git.run("log", "--format=%h %s", span, check=False).stdout.splitlines()
    if not commits:
        ui.ok(f"Nothing to publish: {shared.name} already has your commits.")
        return 0
    entries = [line for line in git.run("log", "--diff-filter=A", "--name-only", "--format=", span, "--",
                                        LEDGER_DIR, check=False).stdout.splitlines() if line.strip()]
    tags = git.run("tag", "--merged", "HEAD", *(["--no-merged", ref] if ref else []), check=False).stdout.split()

    ui.header("sync push", f"to {shared.name}")
    ui.info(f"  {len(commits)} commit(s):")
    for line in commits[:15]:
        ui.info(f"    {line}")
    if len(commits) > 15:
        ui.info(f"    …and {len(commits) - 15} more")
    ui.info(f"  {len(entries)} ledger entr{'y' if len(entries) == 1 else 'ies'} (what teammates will see in 'ralph sync')")
    if tags:
        ui.info(f"  Tags: {', '.join(tags)}")
    finished = _finished_claims(project, git)
    if finished:
        ui.info(f"  Claims released afterwards (their units are done): {', '.join(c.unit for c in finished)}")
    uncommitted = git.status()
    if uncommitted:
        ui.info(f"  Not included: {len(uncommitted)} uncommitted file(s) stay local.")
    running = RunLock(project.lock).holder()
    if running:
        ui.warn("A loop is running; only what it has committed so far is published.")
    if not args.yes and not ui.confirm(f"\n  Push to {shared.name}?", default=False):
        ui.info("  Nothing was pushed.")
        return 0
    result = remote.push(git, shared, tags)
    if result.returncode != 0:
        rejected = "rejected" in (result.stdout + result.stderr) or "non-fast-forward" in result.stderr
        ui.error(f"The push failed: {(result.stderr or result.stdout).strip()[:300]}",
                 "Someone pushed first: run 'ralph sync', then push again." if rejected else "")
        return 1
    ui.ok(f"Published {len(commits)} commit(s) to {shared.name}.")
    if finished:
        released = release_claims(git, shared.remote, config.team.refs, finished)
        if released:
            ui.ok(f"Released the claims on {', '.join(released)}.")
    return 0


def _finished_claims(project: ProjectPaths, git: Git) -> list:
    """Your claims on units that are done, in this clone: they can go once the work is published."""
    email = _email(git)
    done = {(u.cr, u.id) for u, _ in _all_units(project) if u.status is Status.DONE}
    return [c for c in read_claims(git) if c.held_by(email) and (c.cr, c.unit) in done]


# ── views (local only) ───────────────────────────────────────────────────────

def _all_entries(git: Git, config) -> tuple[list, str, Shared | None]:
    shared, ref = local_ref(git, config.team)
    return read_local(git) + read_pending(git, ref), ref, shared


def run_changes(args) -> int:
    project, config = load_project()
    git = Git(project.root)
    entries, _, shared = _all_entries(git, config)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    recent = [e for e in entries if e.pending or (e.when and e.when >= since)]
    ui.header("changes", f"the last {args.days} days · {shared.name + ' as of your last fetch' if shared else 'local only'}")
    if not recent:
        ui.info("  Nothing in the ledger for that period.")
        return 0
    me = _email(git)
    for entry in sorted(recent, key=lambda e: e.at, reverse=True):
        ui.info(_entry_line(entry, me))
        if entry.summary:
            ui.info(f"      {entry.summary.splitlines()[0][:110]}")
    return 0


def run_impact(args) -> int:
    project, config = load_project()
    git = Git(project.root)
    cr, store = load_cr(project, args.cr)
    unit = find_unit(require_units(cr, store), args.unit)
    plan = parse_plan(cr.plan) if cr.plan.exists() else None
    plan_unit = plan.unit(unit.id) if plan else None
    ui.header(f"impact — {unit.id} {unit.title}")

    if unit.status is not Status.DONE:
        view = team_view(git, config.team, _email(git))
        report = assess(view, unit, plan_unit.section if plan_unit else unit.description, project.root)
        ui.info(f"  Teammates' changes, as of your last fetch ({age(view.last_fetch)}):")
        for impact in report.impacts:
            ui.info(f"    {impact.line}")
        if not report.impacts:
            ui.info("    none")
        if report.conflicts_here:
            ui.warn(f"Conflicts with the shared branch in this unit's files: {', '.join(report.conflicts_here)}")
        return 0

    own = [e for e in read_local(git) if e.unit == unit.id and e.cr == cr.name]
    if not own:
        ui.info("  This unit has no ledger entry (it was finished before the ledger existed).")
        return 0
    entry = own[-1]
    ui.info(f"  Changed {len(entry.files)} file(s); interfaces: "
            f"{', '.join(i['name'] + (' (breaking)' if i.get('breaking') else '') for i in entry.interfaces) or 'none'}")
    hits = []
    for other, text in _open_units(project):
        overlap = [p for p in entry.paths if touches(p, other.key_files + other.wip_paths)]
        if overlap:
            hits.append(f"    {other.cr}/{other.id:<6} {other.title[:40]:<40} plans {', '.join(overlap[:3])}")
    ui.info("  Open units it affects (in this clone):")
    ui.info("\n".join(hits) if hits else "    none")
    return 0


def run_team(args) -> int:
    project, config = load_project()
    git = Git(project.root)
    entries, _, shared = _all_entries(git, config)
    since = datetime.now(timezone.utc) - timedelta(days=args.days)
    recent = [e for e in entries if e.pending or (e.when and e.when >= since)]
    ui.header("team", f"the last {args.days} days · "
                      f"{shared.name + ', as of your last fetch (' + age(last_fetch(git)) + ')' if shared else 'local only'}")
    by_dev: dict[str, list] = defaultdict(list)
    for entry in recent:
        by_dev[entry.dev or entry.email].append(entry)
    if not by_dev:
        ui.info("  Nothing in the ledger for that period.")
    for dev, items in sorted(by_dev.items()):
        units = ", ".join(f"{e.cr}/{e.unit}" for e in sorted(items, key=lambda e: e.at)[-6:])
        ui.info(f"  {dev:<20} {len(items)} unit(s), last {max(e.at for e in items)[:10]}: {units}")

    files: dict[str, set] = defaultdict(set)
    for entry in recent:
        for path in entry.paths:
            files[path].add(entry.dev or entry.email)
    claims, locks, email = read_claims(git), read_locks(git), _email(git)
    if claims:
        ui.info("\n  Claims (as of your last sync):")
        for claim in sorted(claims, key=lambda c: (c.cr, c.unit)):
            who = "you" if claim.held_by(email) else claim.dev or claim.email
            state = "expired" if claim.expired() else f"until {claim.expires_at[:16].replace('T', ' ')}"
            ui.info(f"    {claim.cr}/{claim.unit:<10} {who:<18} {state}")
    if locks:
        ui.info("\n  Locks:")
        for lock in sorted(locks, key=lambda l: l.path):
            who = "you" if lock.held_by(email) else lock.dev or lock.email
            ui.info(f"    {lock.path:<30} {who:<18} {lock.reason}")

    shared_files = sorted((p, devs) for p, devs in files.items() if len(devs) > 1)
    ui.info(f"\n  Files more than one of you changed: {len(shared_files)}")
    for path, devs in shared_files[:15]:
        ui.info(f"    {path}  ({', '.join(sorted(devs))})")
    if shared_files:
        ui.info("  If this list keeps growing, claim units ('ralph claim') and lock shared files ('ralph lock').")
    return 0
