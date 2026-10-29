"""Commit what an agent run changed — and only that.

Before a run, the harness snapshots the working tree: which paths were
already dirty, and their contents. Afterwards, the paths the run changed are
the newly dirty ones plus already-dirty ones whose contents changed. Files
you had left uncommitted are never swept into a unit's commit (unless the
agent edited them too, which is reported).

If the agent committed despite the guardrails, its commits are undone with
`git reset --soft`, so its changes stay in the tree and go through the
harness's checks like any others.

When a unit is given up on, its work is saved under
`refs/ralph/rejected/<unit>/<attempt>` before the tree is cleaned, so
nothing is lost and the next unit starts clean.
"""

import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from ralph.vcs.git import Git

# Untracked files that tools create as a side effect (running tests, installing
# packages). They're never part of a unit, even in a repo without a .gitignore for them.
BUILD_ARTIFACT = re.compile(
    r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|node_modules|\.tox)/"
    r"|\.py[co]$|(^|/)\.DS_Store$|(^|/)\.coverage(\.[^/]*)?$")


@dataclass
class Snapshot:
    head: str
    dirty: dict[str, str] = field(default_factory=dict)     # path → status code
    hashes: dict[str, str] = field(default_factory=dict)    # path → content hash


@dataclass
class RunChanges:
    paths: list[str]                                         # changed by the run
    overlapping: list[str] = field(default_factory=list)    # were already dirty; the run changed them too
    agent_commits_undone: int = 0


def take_snapshot(git: Git) -> Snapshot:
    dirty = git.status()
    return Snapshot(head=git.head(), dirty=dirty, hashes=git.hash_files(list(dirty)))


def undo_agent_commits(git: Git, snapshot: Snapshot) -> int:
    """If HEAD moved during the run, move it back and keep the changes in the tree."""
    head = git.head()
    if not snapshot.head or head == snapshot.head:
        return 0
    count = int(git.out("rev-list", "--count", f"{snapshot.head}..{head}") or 0)
    # The agent's changes stay staged; commit_paths commits exactly the run's
    # paths, so anything else you had staged is left as it was.
    git.run("reset", "--soft", snapshot.head)
    return count


def run_changes(git: Git, snapshot: Snapshot, own_paths=(),
                ignore: tuple[str, ...] = ("log/", ".ralph/")) -> RunChanges:
    """What the run changed. `own_paths` are the unit's work from earlier attempts:
    already dirty, but the unit's, so changing them again isn't an overlap with yours."""
    own = set(own_paths)
    now = git.status()
    hashes = git.hash_files(list(now))
    paths, overlapping = [], []
    for path, code in sorted(now.items()):
        if path.startswith(ignore) or (code == "??" and BUILD_ARTIFACT.search(path)):
            continue
        if path not in snapshot.dirty:
            paths.append(path)
        elif hashes.get(path) != snapshot.hashes.get(path) or code != snapshot.dirty[path]:
            paths.append(path)
            if path not in own:
                overlapping.append(path)
    # Paths that were dirty before and the run made clean again (e.g. reverted).
    for path in snapshot.dirty:
        if path not in now and not path.startswith(ignore):
            paths.append(path)
            if path not in own:
                overlapping.append(path)
    return RunChanges(paths=paths, overlapping=overlapping)


def changed_entries(git: Git, paths: list[str]) -> list[tuple[str, str]]:
    """(status letter, path) for each changed path, for the integrity check."""
    status = git.status()
    entries = []
    for path in paths:
        code = status.get(path, " D")
        letter = "A" if code == "??" else (code[1] if code[1] != " " else code[0])
        entries.append((letter, path))
    return entries


def committable(git: Git, paths: list[str]) -> list[str]:
    """The paths git can still add: changed or existing, and not ignored.

    A unit's earlier work can stop being addable, e.g. when a later attempt
    adds a .gitignore rule for it or deletes a file it created.
    """
    status = git.status()
    present = sorted({p for p in paths if p in status or (git.root / p).exists()})
    ignored = git.ignored(present)
    return [p for p in present if p not in ignored and not (status.get(p) == "??" and BUILD_ARTIFACT.search(p))]


def diff_text(git: Git, paths: list[str]) -> str:
    """A unified diff of the run's changes, new files included."""
    paths = committable(git, paths)
    if not paths:
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git.run("read-tree", "HEAD", env=env, check=False)
        git.run("add", "-A", "--", *paths, env=env, check=False)
        return git.run("diff", "--cached", "--no-color", "HEAD", "--", *paths, env=env, check=False).stdout


def commit_paths(git: Git, paths: list[str], message: str) -> str:
    """Commit exactly these paths; return the new commit's hash, or "" if there was nothing to commit.

    Never runs `git commit` without paths: that would commit everything staged,
    including work of yours that has nothing to do with the unit.
    """
    paths = committable(git, paths)
    if not paths:
        return ""
    git.run("add", "-A", "--", *paths)
    if not git.run("diff", "--cached", "--quiet", "--", *paths, check=False).returncode:
        return ""   # nothing actually changed in these paths
    git.run("commit", "--quiet", "-m", message, "--", *paths)
    return git.head()


def save_rejected(git: Git, unit_id: str, attempt: int, paths: list[str]) -> str:
    """Store the run's work as a commit under refs/ralph/rejected/, without touching
    the working tree or the index. Returns the ref name."""
    ref = f"refs/ralph/rejected/{unit_id}/{attempt}"
    paths = committable(git, paths)
    with tempfile.TemporaryDirectory() as tmp:
        env = {"GIT_INDEX_FILE": str(Path(tmp) / "index")}
        git.run("read-tree", "HEAD", env=env)
        if paths:
            git.run("add", "-A", "--", *paths, env=env)
        tree = git.run("write-tree", env=env).stdout.strip()
        commit = git.run("commit-tree", tree, "-p", "HEAD", "-m",
                         f"ralph: work saved from {unit_id} attempt {attempt}", env=env).stdout.strip()
    git.run("update-ref", ref, commit)
    return ref


def clean_paths(git: Git, changes: RunChanges) -> None:
    """Undo the run's changes in the working tree, leaving your own uncommitted
    work (already-dirty paths) alone."""
    own = [p for p in changes.paths if p not in changes.overlapping]
    tracked = set(git.run("ls-files", "--", *own, check=False).stdout.split()) if own else set()
    restore = [p for p in own if p in tracked]
    if restore:
        git.run("restore", "--source=HEAD", "--staged", "--worktree", "--", *restore)
    for path in own:
        if path not in tracked and (git.root / path).is_file():
            os.remove(git.root / path)
