"""Find the project's skills and read their frontmatter."""

from dataclasses import dataclass, field
from pathlib import Path

from ralph import frontmatter
from ralph.fsutil import read_text


@dataclass
class Skill:
    dir: Path
    name: str
    description: str
    body: str
    meta: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.dir / "SKILL.md"

    @property
    def lines(self) -> int:
        return len(self.body.splitlines())

    @property
    def paths(self) -> list[str]:
        value = self.meta.get("paths", [])
        return value if isinstance(value, list) else [p.strip() for p in str(value).split(",") if p.strip()]

    @property
    def versions(self) -> dict[str, str]:
        """The dependency versions the skill was written for: `ralph-versions:` in its frontmatter.
        Claude Code ignores unknown frontmatter fields, so this is safe to add."""
        value = self.meta.get("ralph-versions", {})
        return value if isinstance(value, dict) else {}


def skills_dir(root: Path) -> Path:
    return root / ".claude" / "skills"


def load_skill(directory: Path) -> Skill:
    meta, body = frontmatter.split(read_text(directory / "SKILL.md"))
    return Skill(dir=directory, name=str(meta.get("name", "")), description=str(meta.get("description", "")),
                 body=body, meta=meta)


def discover(root: Path) -> list[Skill]:
    return [load_skill(path.parent) for path in sorted(skills_dir(root).glob("*/SKILL.md"))]
