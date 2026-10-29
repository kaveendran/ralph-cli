"""Turn a recurring lesson into lasting project memory.

Where a lesson goes depends on its shape (docs/meta-harness-plan.md §8.2):
- about one area of the code (it has paths) → a path-scoped rule in
  `.claude/rules/<area>.md`, which Claude loads only when it touches that area;
- a one-line invariant for the whole project → a bullet under `## Invariants`
  in `CLAUDE.md`;
- a multi-step procedure → a skill stub in `.claude/skills/<name>/SKILL.md`.
Nothing is written without your approval (`ralph memory review`).
"""

import re
from dataclasses import dataclass
from pathlib import Path

from ralph import frontmatter
from ralph.fsutil import read_text, write_text_atomic
from ralph.memory.learnings import LearningGroup

PROCEDURE = re.compile(r"(^|\n)\s*(\d+[.)]|-|\*)\s|\bthen\b.*\bthen\b", re.IGNORECASE)


@dataclass
class Proposal:
    target: str           # "rule", "claude_md" or "skill"
    path: Path
    text: str             # what would be added
    why: str


def area_of(paths: list[str]) -> str:
    """The shared top-level folder of the paths, e.g. 'backend', or 'project'."""
    tops = {p.strip("/").split("/")[0].split("*")[0] for p in paths if p.strip("/")}
    tops.discard("")
    return tops.pop() if len(tops) == 1 else "project"


def _slug(text: str, limit: int = 40) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:limit].strip("-") or "lesson"


def propose(group: LearningGroup, root: Path) -> Proposal:
    rule = group.rule.strip()
    if len(rule) > 300 or PROCEDURE.search(rule):
        name = _slug(rule.split(".")[0])
        meta = {"name": name, "description": rule.split("\n")[0][:250]}
        body = f"# {name}\n\n{rule}\n\nLearned from {', '.join(group.units) or 'the build loop'}.\n"
        return Proposal("skill", root / ".claude" / "skills" / name / "SKILL.md",
                        frontmatter.render(meta) + "\n" + body, "a multi-step procedure fits a skill")
    if group.paths:
        area = area_of(group.paths)
        return Proposal("rule", root / ".claude" / "rules" / f"{area}.md", f"- {rule}",
                        f"it applies to {', '.join(group.paths)}, so it loads only when Claude works there")
    return Proposal("claude_md", root / "CLAUDE.md", f"- {rule}", "a one-line rule for the whole project")


def apply(proposal: Proposal, paths_for_rule: list[str] | None = None) -> None:
    path = proposal.path
    if proposal.target == "skill":
        if not path.exists():
            write_text_atomic(path, proposal.text)
        return
    existing = read_text(path)
    if proposal.text.strip() in existing:
        return
    if proposal.target == "rule":
        if not existing:
            meta = {"paths": paths_for_rule or ["**"]}
            existing = frontmatter.render(meta) + f"\n# Rules for {path.stem}\n"
        write_text_atomic(path, existing.rstrip("\n") + "\n" + proposal.text + "\n")
        return
    # CLAUDE.md: add under "## Invariants", creating the section if needed.
    lines = existing.splitlines()
    heading = next((i for i, line in enumerate(lines) if line.strip().lower() == "## invariants"), None)
    if heading is None:
        lines += ["", "## Invariants", proposal.text]
    else:
        end = next((i for i in range(heading + 1, len(lines)) if lines[i].startswith("#")), len(lines))
        while end > heading + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, proposal.text)
    write_text_atomic(path, "\n".join(lines).rstrip("\n") + "\n")
