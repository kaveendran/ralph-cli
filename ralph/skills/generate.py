"""Draft starter skills for the project's stack with Claude, for a human to review.

Claude gets the project map (CLAUDE.md), the stack and the versions in use,
with no tools: it only writes text. Each skill records the versions it was
written for in `ralph-versions:`, so `ralph skills refresh` can tell when an
upgrade makes it stale. Existing skills are never overwritten.
"""

import re
from pathlib import Path

from ralph import frontmatter
from ralph.errors import RalphError
from ralph.fsutil import read_text, write_text_atomic
from ralph.prompts import render
from ralph.runner.analysis import analysis_call
from ralph.runner.base import Runner
from ralph.skills.deps import package_name
from ralph.skills.lint import NAME
from ralph.skills.registry import skills_dir


def generate_stack_skills(runner: Runner, root: Path, stack: str, versions: dict[str, tuple[str, str]],
                          run_dir: Path, max_skills: int = 3) -> list[Path]:
    listed = "\n".join(f"- {name} {version}" for name, (version, _) in sorted(versions.items())[:150]) or "(none found)"
    prompt = render("skill_generate.md", PROJECT_MAP=read_text(root / "CLAUDE.md").strip() or "(no CLAUDE.md yet)",
                    STACK=stack.strip() or "(see the versions)", VERSIONS=listed, MAX_SKILLS=str(max_skills))
    result = runner.run(analysis_call(root, prompt, run_dir, schema_name="stack-skills.json",
                                      max_turns=3, max_budget_usd=2.0))
    drafts = (result.structured or {}).get("skills")
    if not drafts:
        raise RalphError(f"Claude didn't return any skills ({result.subtype or 'no result'}).",
                         f"See {run_dir / 'stream.jsonl'}.")

    written = []
    for draft in drafts[:max_skills]:
        name = re.sub(r"[^a-z0-9]+", "-", draft["name"].lower()).strip("-")
        if not NAME.match(name):
            continue
        path = skills_dir(root) / name / "SKILL.md"
        if path.exists():
            continue
        meta = {"name": name, "description": draft["description"].strip()}
        pinned = {p: versions[package_name(p)][0] for p in draft.get("packages", []) if package_name(p) in versions}
        if pinned:
            meta["ralph-versions"] = {p: ".".join(v.split(".")[:2]) for p, v in pinned.items()}
        body = draft["body"].strip()
        note = "> Drafted by `ralph skills init`. Review it before relying on it; fix any \"Decide:\" lines.\n"
        write_text_atomic(path, frontmatter.render(meta) + "\n" + note + "\n" + body + "\n")
        written.append(path)
    return written
