"""`ralph pr <unit>` — open a draft pull request for one finished unit.

The PR branch, `ralph/pr/<cr>-<unit>`, is the shared branch plus that unit's
own commit, built in a temporary worktree so your files aren't touched. Its
description comes from the unit's ledger entry: the summary, the files, and
any interfaces it changed. It shows what it will push and asks first. Use it
instead of `ralph sync push` when your team reviews every change in a PR
(the GitHub merge queue can then gate them; Claude Code Review comments but
never approves or blocks).
"""

from ralph import ui
from ralph.commands.common import find_unit, load_cr, load_project, require_units
from ralph.errors import RalphError
from ralph.model.unit import Status
from ralph.team import remote
from ralph.team.ledger import read_local
from ralph.team.shared import Shared, shared_branch
from ralph.vcs.git import Git


def register(subparsers) -> None:
    p = subparsers.add_parser("pr", help="open a draft pull request for one finished unit (needs gh)")
    p.add_argument("unit", help="unit ID")
    p.add_argument("--cr", help="change request")
    p.add_argument("--base", help="the branch to merge into (default: the shared branch)")
    p.add_argument("--yes", action="store_true", help="don't ask before pushing")
    p.set_defaults(handler=run)


def describe(unit, entry) -> str:
    lines = ["## Summary", "", entry.summary.strip() if entry and entry.summary else unit.title, ""]
    if unit.handoff:
        lines += ["## Handoff", "", unit.handoff.strip(), ""]
    if entry:
        lines += ["## Files", ""] + [f"- `{f['path']}` ({f['status']})" for f in entry.files[:40]] + [""]
        breaking = [i for i in entry.interfaces if i.get("breaking")]
        if breaking:
            lines += ["## Breaking changes", ""] + [f"- `{i['name']}`: {i.get('change', '')}" for i in breaking] + [""]
    lines += ["---", f"Unit {unit.cr}/{unit.id}, built and verified by the ralph loop (tests run by the harness)."]
    return "\n".join(lines)


def run(args) -> int:
    project, config = load_project()
    cr, store = load_cr(project, args.cr)
    unit = find_unit(require_units(cr, store), args.unit)
    if unit.status is not Status.DONE:
        raise RalphError(f"{unit.id} isn't done yet ({unit.status.value}).", "A PR is for finished units.")
    git = Git(project.root)
    shared = shared_branch(git, config.team)
    base = Shared(shared.remote if shared else config.team.remote, args.base or (shared.branch if shared else ""))
    if not base.branch:
        raise RalphError("I don't know which branch the PR should target.", "Pass --base main (or set [team] branch).")

    entry = next((e for e in reversed(read_local(git)) if e.unit == unit.id and e.cr == cr.name), None)
    commit = entry.commit if entry and entry.commit else git.run(
        "log", "-1", "--format=%H", "--fixed-strings", f"--grep={unit.id}: {unit.title}", check=False).stdout.strip()
    if not commit:
        raise RalphError(f"I can't find {unit.id}'s commit.", "It needs a ledger entry or a commit titled "
                         f"'{unit.id}: {unit.title}'.")
    branch = f"ralph/pr/{cr.name}-{unit.id}"
    remote.fetch(git, base)
    worktree = git.common_dir() / "ralph-pr" / unit.id
    git.run("worktree", "remove", "--force", str(worktree), check=False)
    git.run("branch", "-D", branch, check=False)
    git.run("worktree", "add", "-q", "-b", branch, str(worktree), base.ref)
    picked = Git(worktree).run("cherry-pick", commit, check=False)
    if picked.returncode != 0:
        Git(worktree).run("cherry-pick", "--abort", check=False)
    git.run("worktree", "remove", "--force", str(worktree), check=False)
    if picked.returncode != 0:
        git.run("branch", "-D", branch, check=False)
        raise RalphError(f"{unit.id}'s commit doesn't apply to {base.name} on its own; it builds on commits "
                         "that aren't there yet.", "Publish those first ('ralph sync push'), then open the PR.")

    title, body = f"{unit.id}: {unit.title}", describe(unit, entry)
    ui.header(f"pr — {unit.id}", f"{branch} → {base.name}")
    ui.info(f"  {title}\n")
    for line in body.splitlines()[:25]:
        ui.info(f"    {line}")
    if not args.yes and not ui.confirm(f"\n  Push {branch} to {base.remote} and open a draft PR?", default=False):
        git.run("branch", "-D", branch, check=False)
        ui.info("  Nothing was pushed.")
        return 0
    pushed = remote.push_branch(git, base.remote, branch)
    if pushed.returncode != 0:
        raise RalphError(f"The push failed: {(pushed.stderr or pushed.stdout).strip()[:300]}")
    try:
        created = remote.open_draft_pr(project.root, base.branch, branch, title, body)
    except FileNotFoundError:
        ui.warn(f"Pushed {branch}, but the GitHub CLI (gh) isn't installed, so open the PR yourself.")
        return 0
    if created.returncode != 0:
        ui.warn(f"Pushed {branch}, but gh couldn't open the PR: {(created.stderr or created.stdout).strip()[:300]}")
        return 1
    ui.ok(f"Draft PR: {created.stdout.strip().splitlines()[-1] if created.stdout.strip() else branch}")
    return 0
