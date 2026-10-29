"""The shared branch, as of your last fetch. Local only: nothing here talks to the remote."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ralph.config import TeamConfig
from ralph.vcs.git import Git


@dataclass(frozen=True)
class Shared:
    remote: str
    branch: str

    @property
    def ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.branch}"

    @property
    def name(self) -> str:
        return f"{self.remote}/{self.branch}"


def shared_branch(git: Git, config: TeamConfig) -> Shared | None:
    """`[team] branch`, else the upstream of your current branch."""
    if config.branch:
        return Shared(config.remote, config.branch)
    current = git.branch()
    if not current:
        return None
    remote = git.run("config", f"branch.{current}.remote", check=False).stdout.strip()
    merge = git.run("config", f"branch.{current}.merge", check=False).stdout.strip()
    if remote and remote != "." and merge.startswith("refs/heads/"):
        return Shared(remote, merge[len("refs/heads/"):])
    return None


def has_ref(git: Git, ref: str) -> bool:
    return git.run("rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def local_ref(git: Git, config: TeamConfig) -> tuple[Shared | None, str]:
    """The shared branch and its local ref, or "" when it was never fetched."""
    shared = shared_branch(git, config)
    return shared, shared.ref if shared and has_ref(git, shared.ref) else ""


def ahead_behind(git: Git, ref: str) -> tuple[int, int]:
    """(commits only you have, commits only the shared branch has)."""
    if not ref:
        return len(git.run("rev-list", "HEAD", check=False).stdout.split()), 0
    counts = git.run("rev-list", "--left-right", "--count", f"HEAD...{ref}").stdout.split()
    return int(counts[0]), int(counts[1])


def last_fetch(git: Git) -> datetime | None:
    """When this clone last fetched anything (FETCH_HEAD's time)."""
    path = Path(git.run("rev-parse", "--git-path", "FETCH_HEAD", check=False).stdout.strip())
    path = path if path.is_absolute() else git.root / path
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) if path.is_file() else None
