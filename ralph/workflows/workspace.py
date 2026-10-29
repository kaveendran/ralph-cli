"""Everything a workflow needs about one CR, loaded once."""

from dataclasses import dataclass
from datetime import datetime
from functools import cached_property
from pathlib import Path

from ralph.config import Config
from ralph.errors import RalphError
from ralph.model.plan import Plan, parse_plan
from ralph.model.store import UnitStore
from ralph.model.unit import Unit
from ralph.paths import CrPaths, ProjectPaths
from ralph.vcs.git import Git


@dataclass
class Workspace:
    project: ProjectPaths
    config: Config
    cr: CrPaths

    @property
    def root(self) -> Path:
        return self.project.root

    @cached_property
    def git(self) -> Git:
        return Git(self.root)

    @cached_property
    def store(self) -> UnitStore:
        return UnitStore(self.cr.units_dir)

    @cached_property
    def units(self) -> dict[str, Unit]:
        return self.store.load() if self.store.exists() else {}

    @property
    def plan(self) -> Plan | None:
        return parse_plan(self.cr.plan) if self.cr.plan.exists() else None

    def require_plan(self) -> Plan:
        plan = self.plan
        if plan is None:
            raise RalphError(f"{self.cr.name} has no plan at {self.cr.plan}.",
                             f"Write one with 'ralph start --mode plan --cr {self.cr.name}'.")
        return plan

    def report_path(self, folder: Path, suffix: str) -> Path:
        return folder / f"{datetime.now():%Y%m%d-%H%M%S}-{suffix}.md"

    def run_dir(self, workflow: str) -> Path:
        return self.project.log_dir / f"{workflow}-{datetime.now():%Y%m%d-%H%M%S}"
