"""Size budgets for what Claude loads.

CLAUDE.md, AGENTS.md and unscoped rules load into every session, so they
must stay short; a path-scoped rule loads whenever Claude touches its area.
The docs advise "target under 200 lines per CLAUDE.md file"
(code.claude.com/docs/en/memory) and keeping a SKILL.md body under 500 lines.
"""

from dataclasses import dataclass
from pathlib import Path

from ralph import frontmatter
from ralph.config import MemoryConfig
from ralph.fsutil import read_text
from ralph.memory.learnings import read_inbox


@dataclass
class Finding:
    path: Path
    message: str
    severity: str = "warn"       # "warn" or "error"


def _lines(path: Path) -> int:
    return len(read_text(path).splitlines())


def lint_memory(root: Path, budgets: MemoryConfig, units_dirs: list[Path] = (),
                inbox: Path | None = None) -> list[Finding]:
    findings = []

    def over(path: Path, limit: int, what: str) -> None:
        count = _lines(path)
        if count > limit:
            findings.append(Finding(path, f"{count} lines (budget {limit}); {what}"))

    over(root / "CLAUDE.md", budgets.claude_md_lines,
         "it loads into every session: keep it a map, and move area rules to .claude/rules/")
    over(root / "AGENTS.md", budgets.agents_md_lines,
         "keep only run/build/test commands; move the rest to the docs it points to")
    for rule in sorted((root / ".claude" / "rules").glob("**/*.md")):
        meta, _ = frontmatter.split(read_text(rule))
        scope = "only where its paths match" if meta.get("paths") else "in every session (it has no paths:)"
        over(rule, budgets.rule_lines, f"it loads {scope}")
    for skill in sorted((root / ".claude" / "skills").glob("*/SKILL.md")):
        over(skill, budgets.skill_lines, "move details into reference files next to it")
    for units_dir in units_dirs:
        for notes in sorted(Path(units_dir).glob("*.notes.md")):
            over(notes, budgets.notes_lines, "only the last 40 lines reach the agent; run 'ralph memory decay'")
    waiting = [i for i in read_inbox(inbox) if i.status == "new"] if inbox else []
    if len(waiting) > 30:
        findings.append(Finding(inbox, f"{len(waiting)} lessons wait for review; run 'ralph memory review'"))
    return findings
