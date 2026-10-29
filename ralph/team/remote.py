"""The only code that talks to the remote.

Called only by `ralph sync` and `ralph sync push`, after you ask. The loop
kernel doesn't import this module.
"""

import subprocess

from ralph.team.shared import Shared
from ralph.vcs.git import Git


def fetch(git: Git, shared: Shared) -> None:
    git.run("fetch", "--quiet", shared.remote, f"+refs/heads/{shared.branch}:{shared.ref}")


def push(git: Git, shared: Shared, tags: list[str]) -> subprocess.CompletedProcess:
    """Fast-forward the shared branch to HEAD (never forced), plus the given tags."""
    return git.run("push", "--porcelain", shared.remote, f"HEAD:refs/heads/{shared.branch}",
                   *(f"refs/tags/{t}" for t in tags), check=False)
