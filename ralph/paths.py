"""Where everything lives in a project that ralph manages.

A project has a `development/` folder with one folder per change request
(`initial`, `cr1`, `cr2`, …) and a `.ralph/` folder for harness settings.
All path conventions are defined here and nowhere else.
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ralph.errors import RalphError

CR_NAME = re.compile(r"^(initial|cr\d+)$")


@dataclass(frozen=True)
class CrPaths:
    """The files of one change request."""

    root: Path
    name: str

    @property
    def dir(self) -> Path:
        return self.root / "development" / self.name

    @property
    def spec(self) -> Path:
        return self.dir / "spec" / "spec.md"

    @property
    def plan(self) -> Path:
        """The plan file: the first `*-implementation-plan.md` in `plan/`."""
        matches = sorted((self.dir / "plan").glob("*-implementation-plan.md"))
        return matches[0] if matches else self.dir / "plan" / f"{self.name}-implementation-plan.md"

    @property
    def units_dir(self) -> Path:
        return self.dir / "units"

    @property
    def archive_dir(self) -> Path:
        return self.dir / "archive"

    @property
    def instructions(self) -> Path:
        """CR-specific instructions included in every unit pack."""
        return self.dir / "prompts" / "instructions.md"

    @property
    def plan_prompt(self) -> Path:
        return self.dir / "prompts" / "spec-planning.md"

    @property
    def legacy_progress(self) -> Path:
        return self.dir / "progress" / "implementation-progress.md"

    @property
    def legacy_build_prompt(self) -> Path:
        return self.dir / "prompts" / "build.md"

    @property
    def progress_view(self) -> Path:
        """The generated, read-only progress view (`ralph status --write`)."""
        return self.dir / "PROGRESS.md"

    @property
    def number(self) -> int:
        """1 for cr1; 0 for initial."""
        return int(self.name[2:]) if self.name.startswith("cr") else 0


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    @property
    def ralph_dir(self) -> Path:
        return self.root / ".ralph"

    @property
    def config(self) -> Path:
        return self.ralph_dir / "config.toml"

    @property
    def mcp_config(self) -> Path:
        return self.ralph_dir / "mcp.json"

    @property
    def lock(self) -> Path:
        return self.ralph_dir / "run.lock"

    @property
    def control_dir(self) -> Path:
        """Steering notes and the pause flag, written by `ralph steer` and `ralph pause`."""
        return self.ralph_dir / "control"

    @property
    def constitution(self) -> Path:
        """The project's principles: in every unit pack, and checked by the reviewer."""
        return self.ralph_dir / "constitution.md"

    @property
    def learnings_inbox(self) -> Path:
        """Lessons the agent reported, waiting for `ralph memory review`."""
        return self.ralph_dir / "learnings" / "inbox.jsonl"

    @property
    def log_dir(self) -> Path:
        return self.root / "log"

    @property
    def iterations_log(self) -> Path:
        return self.log_dir / "iterations.jsonl"

    def cr(self, name: str) -> CrPaths:
        return CrPaths(self.root, name)

    def cr_names(self) -> list[str]:
        """All change requests, `initial` first, then cr1, cr2, … in number order."""
        development = self.root / "development"
        if not development.is_dir():
            return []
        names = [p.name for p in development.iterdir() if p.is_dir() and CR_NAME.match(p.name)]
        return sorted(names, key=lambda n: -1 if n == "initial" else int(n[2:]))


def find_project_root(start: Path | None = None) -> Path:
    """The project root: the nearest folder (from `start` up) with `.ralph/` or
    `development/`, else the git top level, else `start` itself."""
    start = (start or Path.cwd()).resolve()
    for folder in (start, *start.parents):
        if (folder / ".ralph").is_dir() or (folder / "development").is_dir():
            return folder
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=start,
                            capture_output=True, text=True)
    return Path(result.stdout.strip()) if result.returncode == 0 else start


def normalise_cr(value: str) -> str:
    """Accept `5`, `cr5`, `CR5` or `initial`."""
    value = value.strip().lower()
    if value.isdigit():
        value = f"cr{value}"
    if not CR_NAME.match(value):
        raise RalphError(f"'{value}' isn't a change request name.",
                         "Use a number like 5, a name like cr5, or 'initial'.")
    return value


def resolve_cr(project: ProjectPaths, given: str | None) -> str:
    """The CR to work on: the given one, else the latest CR that has units,
    else the latest that has a plan, else the latest that has a spec (a new CR)."""
    if given:
        name = normalise_cr(given)
        if not project.cr(name).dir.is_dir():
            raise RalphError(f"There is no {project.root / 'development' / name}.",
                             f"Create it with 'ralph add-cr --number {name[2:] or 'N'}'.")
        return name
    names = project.cr_names()
    for check in (lambda c: c.units_dir.is_dir(), lambda c: c.plan.exists(), lambda c: c.spec.exists()):
        found = [n for n in names if check(project.cr(n))]
        if found:
            return found[-1]
    raise RalphError("No change request found under development/.",
                     "Run 'ralph init' for a new project, or 'ralph add-cr' to add one.")
