"""Find skills and vendored docs written for versions the project no longer uses.

Stale guidance is worse than none: incorrect information is the worst thing
to have in context. A skill records its versions in `ralph-versions:`; a
vendored doc folder is named `docs/vendor/<package>@<version>/`. A pin
matches when the installed version starts with it at a dot boundary:
"0.115" matches 0.115.4 but not 0.116.0.
"""

from dataclasses import dataclass
from pathlib import Path

from ralph.skills.deps import package_name
from ralph.skills.registry import Skill


@dataclass
class Stale:
    item: str             # skill name or doc folder
    package: str
    written_for: str
    installed: str
    source: str           # the file the installed version came from


def version_matches(pinned: str, installed: str) -> bool:
    pinned, installed = pinned.strip().lstrip("v^~="), installed.strip().lstrip("v^~=")
    return installed == pinned or installed.startswith(pinned + ".")


def stale_skills(skills: list[Skill], versions: dict[str, tuple[str, str]]) -> list[Stale]:
    found = []
    for skill in skills:
        for package, pinned in skill.versions.items():
            current = versions.get(package_name(package))
            if current and not version_matches(str(pinned), current[0]):
                found.append(Stale(skill.name, package, str(pinned), current[0], current[1]))
    return found


def stale_docs(root: Path, versions: dict[str, tuple[str, str]]) -> list[Stale]:
    found = []
    for folder in sorted((root / "docs" / "vendor").glob("*@*")):
        package, _, pinned = folder.name.rpartition("@")
        current = versions.get(package_name(package))
        if folder.is_dir() and current and not version_matches(pinned, current[0]):
            found.append(Stale(str(folder.relative_to(root)), package, pinned, current[0], current[1]))
    return found
