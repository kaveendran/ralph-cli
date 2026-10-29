"""`ralph claim`, `ralph release`, `ralph lock`, `ralph unlock` (docs/meta-harness-plan.md §11.3).

Each one fetches the remote's claims and locks, shows what it will push,
asks, then pushes only the refs involved (never your branch). Taking a claim
or a lock is atomic: if a teammate got there first, the push is refused and
nothing changes. A claim lasts `[team] lease_hours` (48 by default); renew
it with `--renew`, or take over an expired one with `--take-over`.
"""

from ralph import ui
from ralph.commands.common import find_unit, load_cr, load_project, require_units
from ralph.errors import RalphError
from ralph.graph.schedule import ready_units
from ralph.model.unit import Status
from ralph.team import remote
from ralph.team.claims import (claim_ref, lock_ref, mirror_of, new_claim, new_lock, read_claims, read_locks,
                               record_commit)
from ralph.vcs.git import Git


def register(subparsers) -> None:
    c = subparsers.add_parser("claim", help="reserve units on the shared remote so teammates don't build them too")
    c.add_argument("units", nargs="*", metavar="ID", help="units to claim")
    c.add_argument("--cr", help="change request")
    c.add_argument("--next", type=int, metavar="N", help="claim the next N ready units nobody holds")
    c.add_argument("--renew", action="store_true", help="extend your claims (all of them, or the ones named)")
    c.add_argument("--take-over", metavar="ID", help="take over a teammate's expired claim")
    c.add_argument("--hours", type=int, help="lease length (default [team] lease_hours)")
    c.add_argument("--yes", action="store_true", help="don't ask before pushing")
    c.set_defaults(handler=run_claim)

    r = subparsers.add_parser("release", help="give up your claims")
    r.add_argument("units", nargs="*", metavar="ID", help="units to release")
    r.add_argument("--cr", help="change request")
    r.add_argument("--done", action="store_true", help="release your claims on units that are done")
    r.add_argument("--yes", action="store_true", help="don't ask before pushing")
    r.set_defaults(handler=run_release)

    lk = subparsers.add_parser("lock", help="reserve a path only you should change for now (e.g. migrations/)")
    lk.add_argument("path", help="a file, folder or glob, relative to the project root")
    lk.add_argument("--reason", default="", help="why, for teammates")
    lk.add_argument("--yes", action="store_true", help="don't ask before pushing")
    lk.set_defaults(handler=run_lock)

    un = subparsers.add_parser("unlock", help="release a lock you hold")
    un.add_argument("path")
    un.add_argument("--yes", action="store_true", help="don't ask before pushing")
    un.set_defaults(handler=run_unlock)


def _identity(git: Git) -> tuple[str, str]:
    name = git.run("config", "user.name", check=False).stdout.strip()
    email = git.run("config", "user.email", check=False).stdout.strip()
    if not email:
        raise RalphError("git has no user.email, so a claim couldn't say whose it is.",
                         "Set it with: git config user.email you@example.com")
    return name, email


def _connect(project, config) -> tuple[Git, str, str]:
    """(git, remote, namespace), after fetching the remote's claims and locks."""
    git = Git(project.root)
    remote_name = config.team.remote
    if remote_name not in git.run("remote", check=False).stdout.split():
        raise RalphError(f"There's no git remote '{remote_name}' to share claims through.",
                         "Add one (git remote add origin <url>) or set [team] remote.")
    remote.fetch_team_refs(git, remote_name, config.team.refs)
    return git, remote_name, config.team.refs


def _confirm(lines: list[str], question: str, yes: bool) -> bool:
    for line in lines:
        ui.info(f"  {line}")
    return yes or ui.confirm(f"\n  {question}", default=False)


def _sync_mirror(git: Git, ref: str, namespace: str, sha: str) -> None:
    mirror = mirror_of(ref, namespace)
    if sha:
        git.run("update-ref", mirror, sha)
    else:
        git.run("update-ref", "-d", mirror, check=False)


def run_claim(args) -> int:
    project, config = load_project()
    cr, store = load_cr(project, args.cr)
    units = require_units(cr, store)
    git, remote_name, namespace = _connect(project, config)
    dev, email = _identity(git)
    held = {c.unit: c for c in read_claims(git) if c.cr == cr.name}
    hours = args.hours if args.hours is not None else config.team.lease_hours

    if args.take_over:
        targets, mode = [find_unit(units, args.take_over).id], "take over"
    elif args.renew:
        targets = [u for u in (args.units or held) if u in held and held[u].held_by(email)]
        mode = "renew"
    elif args.next:
        free = [u.id for u in ready_units(units) if u.id not in held or held[u.id].expired()]
        targets, mode = free[:args.next], "claim"
    else:
        targets, mode = [find_unit(units, u).id for u in args.units], "claim"
    if not targets:
        ui.info("  Nothing to claim." if mode == "claim" else "  You hold no claims to renew.")
        return 0

    plan = []
    for unit_id in targets:
        current = held.get(unit_id)
        if mode == "take over":
            if current is None or not current.expired():
                raise RalphError(f"{unit_id} isn't held by an expired claim.", "'ralph team' shows the claims.")
        elif mode == "claim" and current is not None and not current.expired():
            ui.warn(f"{unit_id} is already claimed by {'you' if current.held_by(email) else current.dev or current.email}"
                    f" until {current.expires_at[:16].replace('T', ' ')}.")
            continue
        elif mode == "claim" and current is not None:
            ui.warn(f"{unit_id}'s claim by {current.dev or current.email} has expired; use --take-over {unit_id}.")
            continue
        if units[unit_id].status is Status.DONE:
            ui.warn(f"{unit_id} is already done.")
            continue
        plan.append(unit_id)
    if not plan:
        return 1
    if not _confirm([f"{mode} {cr.name}/{u} for {hours} h ({units[u].title})" for u in plan],
                    f"Push {len(plan)} claim(s) to {remote_name}?", args.yes):
        ui.info("  Nothing was pushed.")
        return 0

    failed = 0
    for unit_id in plan:
        unit, current = units[unit_id], held.get(unit_id)
        claim = new_claim(unit_id, cr.name, dev, email, unit.key_files + unit.wip_paths, git.head(), hours)
        sha = record_commit(git, "claim.json", claim.to_json(), f"ralph: {email} claims {cr.name}/{unit_id}")
        ref = claim_ref(namespace, cr.name, unit_id)
        if remote.swap_ref(git, remote_name, ref, sha, current.sha if current and mode != "claim" else ""):
            _sync_mirror(git, ref, namespace, sha)
            ui.ok(f"{'Renewed' if mode == 'renew' else 'Claimed'} {unit_id} until {claim.expires_at[:16].replace('T', ' ')}.")
        else:
            failed += 1
            ui.warn(f"{unit_id}: a teammate changed its claim first; run 'ralph team' to see who has it.")
    return 1 if failed else 0


def release_claims(git: Git, remote_name: str, namespace: str, claims) -> list[str]:
    """Delete these claims on the remote (only if they're still the ones you hold). Returns the units released."""
    released = []
    for claim in claims:
        ref = claim_ref(namespace, claim.cr, claim.unit)
        if remote.swap_ref(git, remote_name, ref, "", claim.sha):
            _sync_mirror(git, ref, namespace, "")
            released.append(claim.unit)
        else:
            ui.warn(f"{claim.unit}: the claim changed on the remote, so it wasn't released.")
    return released


def run_release(args) -> int:
    project, config = load_project()
    cr, store = load_cr(project, args.cr)
    units = require_units(cr, store)
    git, remote_name, namespace = _connect(project, config)
    _, email = _identity(git)
    mine = [c for c in read_claims(git) if c.cr == cr.name and c.held_by(email)]
    if args.done:
        mine = [c for c in mine if c.unit in units and units[c.unit].status is Status.DONE]
    elif args.units:
        mine = [c for c in mine if c.unit in {find_unit(units, u).id for u in args.units}]
    if not mine:
        ui.info("  You hold no matching claims.")
        return 0
    if not _confirm([f"release {cr.name}/{c.unit}" for c in mine], f"Push to {remote_name}?", args.yes):
        ui.info("  Nothing was pushed.")
        return 0
    released = release_claims(git, remote_name, namespace, mine)
    if released:
        ui.ok(f"Released {', '.join(released)}.")
    return 0 if len(released) == len(mine) else 1


def run_lock(args) -> int:
    project, config = load_project()
    git, remote_name, namespace = _connect(project, config)
    dev, email = _identity(git)
    path = args.path.strip().strip("/")
    current = next((lock for lock in read_locks(git) if lock.path == path), None)
    if current is not None:
        who = "you" if current.held_by(email) else current.dev or current.email
        ui.warn(f"{path} is already locked by {who} since {current.locked_at[:16].replace('T', ' ')}.")
        return 1
    if not _confirm([f"lock {path}" + (f" ({args.reason})" if args.reason else "")],
                    f"Push the lock to {remote_name}?", args.yes):
        ui.info("  Nothing was pushed.")
        return 0
    lock = new_lock(path, dev, email, args.reason)
    sha = record_commit(git, "lock.json", lock.to_json(), f"ralph: {email} locks {path}")
    ref = lock_ref(namespace, path)
    if not remote.swap_ref(git, remote_name, ref, sha, ""):
        ui.warn(f"A teammate locked {path} first; run 'ralph team' to see who.")
        return 1
    _sync_mirror(git, ref, namespace, sha)
    ui.ok(f"Locked {path}. Teammates' loops won't edit it once they sync; 'ralph unlock {path}' releases it.")
    return 0


def run_unlock(args) -> int:
    project, config = load_project()
    git, remote_name, namespace = _connect(project, config)
    _, email = _identity(git)
    path = args.path.strip().strip("/")
    current = next((lock for lock in read_locks(git) if lock.path == path), None)
    if current is None:
        ui.info(f"  {path} isn't locked.")
        return 0
    if not current.held_by(email):
        raise RalphError(f"{path} is locked by {current.dev or current.email}, not you.", "Ask them to unlock it.")
    if not _confirm([f"unlock {path}"], f"Push to {remote_name}?", args.yes):
        ui.info("  Nothing was pushed.")
        return 0
    ref = lock_ref(namespace, path)
    if not remote.swap_ref(git, remote_name, ref, "", current.sha):
        ui.warn(f"The lock on {path} changed on the remote, so it wasn't released.")
        return 1
    _sync_mirror(git, ref, namespace, "")
    ui.ok(f"Unlocked {path}.")
    return 0
