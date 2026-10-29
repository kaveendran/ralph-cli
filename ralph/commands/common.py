"""Helpers shared by the commands."""

from ralph.config import Config, load_config
from ralph.errors import RalphError
from ralph.model.store import UnitStore
from ralph.model.unit import Unit
from ralph.paths import CrPaths, ProjectPaths, find_project_root, resolve_cr
from ralph.vcs import commit as vcs
from ralph.vcs.git import Git


def load_project() -> tuple[ProjectPaths, Config]:
    project = ProjectPaths(find_project_root())
    return project, load_config(project.config)


def load_cr(project: ProjectPaths, given: str | None) -> tuple[CrPaths, UnitStore]:
    cr = project.cr(resolve_cr(project, given))
    return cr, UnitStore(cr.units_dir)


def require_units(cr: CrPaths, store: UnitStore) -> dict[str, Unit]:
    if not store.exists():
        hint = (f"This CR uses the old progress file. Run 'ralph migrate --cr {cr.name}' (on a branch), "
                f"or 'ralph legacy start --cr {cr.name}' for the previous loop."
                if cr.legacy_progress.exists() else
                f"Write a plan with 'ralph start --mode plan --cr {cr.name}', then 'ralph graph compile'.")
        raise RalphError(f"{cr.name} has no units yet.", hint)
    return store.load()


def find_unit(units: dict[str, Unit], unit_id: str) -> Unit:
    unit = units.get(unit_id) or units.get(unit_id.upper())
    if unit is None:
        raise RalphError(f"There's no unit {unit_id}.", "List them with 'ralph graph'.")
    return unit


def commit_state(project: ProjectPaths, store: UnitStore, units: list[Unit], message: str) -> str:
    """Commit unit state changed by a command, so git always reflects it."""
    git = Git(project.root)
    if not git.is_repo():
        return ""
    paths = [str(p.relative_to(project.root)) for u in units
             for p in (store.path(u.id), store.notes_path(u.id)) if p.exists()]
    return vcs.commit_paths(git, paths, message)
