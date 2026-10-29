"""A thin, readable wrapper around the git commands the harness uses."""

import os
import subprocess
from pathlib import Path

from ralph.errors import RalphError


class Git:
    def __init__(self, root: Path):
        self.root = root

    def run(self, *args: str, check: bool = True, env: dict | None = None,
            input_text: str | None = None) -> subprocess.CompletedProcess:
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True,
                                env={**os.environ, **env} if env else None, input=input_text)
        if check and result.returncode != 0:
            raise RalphError(f"git {' '.join(args[:3])} failed: {(result.stderr or result.stdout).strip()[:500]}")
        return result

    def out(self, *args: str) -> str:
        return self.run(*args).stdout.strip()

    def is_repo(self) -> bool:
        return self.run("rev-parse", "--is-inside-work-tree", check=False).returncode == 0

    def head(self) -> str:
        result = self.run("rev-parse", "HEAD", check=False)
        return result.stdout.strip() if result.returncode == 0 else ""

    def branch(self) -> str:
        return self.run("branch", "--show-current", check=False).stdout.strip()

    def default_branch(self) -> str:
        """origin's default branch (from origin/HEAD), else main or master if present."""
        ref = self.run("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False).stdout.strip()
        if ref:
            return ref.rsplit("/", 1)[-1]
        for name in ("main", "master"):
            if self.run("show-ref", "--verify", "--quiet", f"refs/heads/{name}", check=False).returncode == 0:
                return name
        return ""

    def status(self) -> dict[str, str]:
        """Changed paths and their two-letter status, untracked files included."""
        output = self.run("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
        entries, parts, index = {}, output.split("\0"), 0
        while index < len(parts):
            entry = parts[index]
            index += 1
            if len(entry) < 4:
                continue
            code, path = entry[:2], entry[3:]
            if code[0] in "RC":     # a rename: the next part is the old path
                index += 1
            entries[path] = code
        return entries

    def hash_files(self, paths: list[str]) -> dict[str, str]:
        """Content hashes for paths that exist (deleted paths are left out)."""
        existing = [p for p in paths if (self.root / p).is_file()]
        if not existing:
            return {}
        output = self.run("hash-object", "--stdin-paths", input_text="\n".join(existing) + "\n").stdout
        return dict(zip(existing, output.split()))

    def ignored(self, paths: list[str]) -> set[str]:
        """The paths that .gitignore rules exclude (tracked files never count as ignored)."""
        if not paths:
            return set()
        result = self.run("check-ignore", "--stdin", check=False, input_text="\n".join(paths) + "\n")
        return set(result.stdout.split("\n")) - {""}

    def common_dir(self) -> Path:
        """The repository's shared .git folder, also from inside a worktree."""
        path = Path(self.out("rev-parse", "--git-common-dir"))
        return path if path.is_absolute() else self.root / path

    def ensure_attributes(self, lines: tuple[str, ...]) -> None:
        """Add gitattributes lines for this clone only (.git/info/attributes, never committed)."""
        path = self.common_dir() / "info" / "attributes"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        missing = [line for line in lines if line not in existing.splitlines()]
        if missing:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(existing + ("" if not existing or existing.endswith("\n") else "\n")
                            + "".join(line + "\n" for line in missing), encoding="utf-8")

    def user_configured(self) -> bool:
        return bool(self.run("config", "user.email", check=False).stdout.strip())
