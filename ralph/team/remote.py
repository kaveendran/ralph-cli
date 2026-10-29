"""The only code that talks to the remote.

Called only by `ralph sync`, `ralph sync push`, `ralph claim`, `ralph release`,
`ralph lock`, `ralph unlock` and `ralph pr`, after you ask. The loop kernel
doesn't import this module.
"""

import subprocess

from ralph.team.claims import MIRROR
from ralph.team.shared import Shared
from ralph.vcs.git import Git


def fetch(git: Git, shared: Shared) -> None:
    git.run("fetch", "--quiet", shared.remote, f"+refs/heads/{shared.branch}:{shared.ref}")


def fetch_team_refs(git: Git, remote: str, namespace: str) -> None:
    """Copy the remote's claims and locks to refs/ralph/team/, dropping ones it no longer has."""
    git.run("fetch", "--quiet", "--prune", remote, f"+{namespace}/claims/*:{MIRROR}/claims/*",
            f"+{namespace}/locks/*:{MIRROR}/locks/*")


def push(git: Git, shared: Shared, tags: list[str]) -> subprocess.CompletedProcess:
    """Fast-forward the shared branch to HEAD (never forced), plus the given tags."""
    return git.run("push", "--porcelain", shared.remote, f"HEAD:refs/heads/{shared.branch}",
                   *(f"refs/tags/{t}" for t in tags), check=False)


def push_branch(git: Git, remote: str, branch: str) -> subprocess.CompletedProcess:
    return git.run("push", "--porcelain", "--set-upstream", remote, f"refs/heads/{branch}:refs/heads/{branch}",
                   check=False)


def swap_ref(git: Git, remote: str, ref: str, new: str, expected: str) -> bool:
    """Compare and swap one remote ref: set it to `new` ("" deletes it) only if it
    still points to `expected` ("" means it must not exist). True if it worked."""
    source = f"{new}:{ref}" if new else f":{ref}"
    result = git.run("push", "--porcelain", f"--force-with-lease={ref}:{expected}", remote, source, check=False)
    return result.returncode == 0


def open_draft_pr(root, base: str, head: str, title: str, body: str) -> subprocess.CompletedProcess:
    """`gh pr create --draft`. Raises FileNotFoundError when gh isn't installed."""
    return subprocess.run(["gh", "pr", "create", "--draft", "--base", base, "--head", head, "--title", title,
                           "--body", body], cwd=root, capture_output=True, text=True)
