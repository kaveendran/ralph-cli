"""Check skills against Anthropic's skill authoring guidance
(platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices):
a name and a description, a description that says what the skill does and
when to use it (in the third person, short enough for the skill listing),
a body under 500 lines, and references kept one level deep.
"""

import re
from pathlib import Path

from ralph.fsutil import read_text
from ralph.memory.lint import Finding
from ralph.skills.registry import Skill

NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
LOCAL_LINK = re.compile(r"\]\((?!https?:|#)([^)#\s]+\.md)\)")
FIRST_PERSON = re.compile(r"^(i|you|we|my|our|let me)\b", re.IGNORECASE)
TOO_MANY_SKILLS = 15


def lint_skill(skill: Skill, max_lines: int = 500) -> list[Finding]:
    found = []

    def add(message: str, severity: str = "warn") -> None:
        found.append(Finding(skill.path, message, severity))

    if not skill.name:
        add("no `name` in the frontmatter", "error")
    elif not NAME.match(skill.name) or len(skill.name) > 64:
        add(f"name '{skill.name}' should be lowercase words joined by hyphens, at most 64 characters", "error")
    elif skill.name != skill.dir.name:
        add(f"name '{skill.name}' differs from its folder '{skill.dir.name}'")
    if not skill.description:
        add("no `description`: Claude decides when to use a skill from it", "error")
    else:
        if len(skill.description) > 1024:
            add(f"description is {len(skill.description)} characters (limit 1024)", "error")
        elif len(skill.description) > 300:
            add(f"description is {len(skill.description)} characters; keep it under 300, use case first")
        if FIRST_PERSON.match(skill.description):
            add("write the description in the third person (\"Writes …\", not \"I …\" or \"You …\")")
    if skill.lines > max_lines:
        add(f"body is {skill.lines} lines (budget {max_lines}); move details into reference files")

    for target in LOCAL_LINK.findall(skill.body):
        reference = (skill.dir / target).resolve()
        if not reference.exists():
            add(f"links to {target}, which doesn't exist")
            continue
        text = read_text(reference)
        if LOCAL_LINK.search(text):
            add(f"{target} links to further files; keep references one level deep from SKILL.md")
        if len(text.splitlines()) > 100 and "contents" not in text[:1500].lower():
            add(f"{target} is over 100 lines; start it with a table of contents")
    return found


def lint_skills(skills: list[Skill], max_lines: int = 500) -> list[Finding]:
    found = [f for skill in skills for f in lint_skill(skill, max_lines)]
    if len(skills) > TOO_MANY_SKILLS:
        found.append(Finding(Path(".claude/skills"), f"{len(skills)} skills: the listing has a budget, and "
                             "overlapping descriptions make Claude pick the wrong one. Prefer a few focused ones."))
    return found
