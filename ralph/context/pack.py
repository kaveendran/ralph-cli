"""Build the context pack for one unit.

The pack is the only project context the harness puts in front of the
agent: the unit's own plan entry, one-line summaries and handoffs of the
units it depends on, the spec sections it cites, its last failure, and the
CR's instructions. Everything else the agent fetches on demand, ideally
through the `explorer` subagent. Each section has a size budget, so the pack
stays small no matter how large the plan, spec or history grow.
"""

from dataclasses import dataclass, field

from ralph.model.plan import Plan
from ralph.model.spec import Spec, excerpts
from ralph.model.unit import Status, Unit
from ralph.prompts import render

CHARS_PER_TOKEN = 4

# Section budgets, in characters.
UNIT_SECTION_CHARS = 16_000
DEPENDENCY_CHARS = 3_000
SPEC_CHARS = 12_000
LAST_ATTEMPT_CHARS = 4_000
LESSON_CHARS = 2_000
INSTRUCTIONS_CHARS = 8_000


@dataclass
class PackInputs:
    unit: Unit
    units: dict[str, Unit]
    plan: Plan | None
    specs: dict[str, Spec | None]
    instructions: str = ""
    last_attempt: str = ""       # the unit's recent notes: failures, review gaps
    steering: str = ""           # notes from `ralph steer`
    checks: list[tuple[str, str]] = field(default_factory=list)   # (gate name, command) the harness will run
    lessons: list[str] = field(default_factory=list)               # unreviewed lessons that match the unit
    skills: list[tuple[str, str]] = field(default_factory=list)    # (skill name, why it fits)
    branch: str = ""
    head: str = ""


@dataclass
class Pack:
    text: str
    unit_id: str
    sections: dict[str, int] = field(default_factory=dict)   # section → chars
    truncated: list[str] = field(default_factory=list)
    spec_pointers: list[str] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def tokens(self) -> int:
        return self.chars // CHARS_PER_TOKEN


def _cap(name: str, text: str, limit: int, truncated: list[str]) -> str:
    if len(text) <= limit:
        return text
    truncated.append(name)
    return text[:limit].rstrip() + f"\n\n[… {len(text) - limit} more characters cut to fit the context budget]"


def _unit_section(inputs: PackInputs) -> str:
    plan_unit = inputs.plan.unit(inputs.unit.id) if inputs.plan else None
    if plan_unit:
        return plan_unit.section
    # Units without a plan entry: discovered during other work, or migrated.
    lines = [f"## Unit: {inputs.unit.title} (ID: {inputs.unit.id})", ""]
    if inputs.unit.discovered_from:
        lines.append(f"Found while working on {inputs.unit.discovered_from}.")
    lines.append(inputs.unit.description or "(No plan entry. Work from the title and the notes below.)")
    return "\n".join(lines)


def _dependencies(inputs: PackInputs) -> str:
    lines = []
    for dep_id in inputs.unit.depends_on:
        dep = inputs.units.get(dep_id)
        if dep is None:
            continue
        lines.append(f"- **{dep.id}** {dep.title} ({dep.status.value})")
        if dep.status is Status.DONE and dep.handoff:
            lines.extend(f"  > {line}" for line in dep.handoff.strip().splitlines()[:10])
    for ref in inputs.unit.external_deps:
        lines.append(f"- {ref} (another CR; for reference)")
    return "\n".join(lines) or "None."


def _spec_section(inputs: PackInputs, pointers: list[str]) -> str:
    if not inputs.unit.spec_refs:
        return "Your unit cites no spec sections."
    found = excerpts(inputs.unit.spec_refs, inputs.specs, inputs.unit.cr, max_chars=SPEC_CHARS)
    pointers.extend(found.pointers)
    text = found.text or "None of the cited sections could be included."
    if found.pointers:
        text += "\n\nNot included (open these only if you need them):\n" + "\n".join(f"- {p}" for p in found.pointers)
    return text


def format_checks(checks: list[tuple[str, str]]) -> str:
    if not checks:
        return "  - (none configured: the harness takes your word and the reviewer's)"
    return "\n".join(f"  - `{command}` ({name})" for name, command in checks)


def build_pack(inputs: PackInputs) -> Pack:
    unit, units = inputs.unit, inputs.units
    truncated: list[str] = []
    pointers: list[str] = []
    done = sum(1 for u in units.values() if u.status is Status.DONE)

    sections = {
        "UNIT_SECTION": _cap("unit section", _unit_section(inputs), UNIT_SECTION_CHARS, truncated),
        "DEPENDENCIES": _cap("dependencies", _dependencies(inputs), DEPENDENCY_CHARS, truncated),
        "SPEC_EXCERPTS": _spec_section(inputs, pointers),
        "LESSONS": _cap("lessons", "\n".join(f"- {lesson}" for lesson in inputs.lessons) or "None recorded yet.",
                        LESSON_CHARS, truncated),
        "SKILLS": "\n".join(f"- `{name}`: {why}" for name, why in inputs.skills)
                  or "None in particular; use any project skill whose description fits.",
        "LAST_ATTEMPT": _cap("last attempt", inputs.last_attempt.strip() or "This is the first attempt.",
                             LAST_ATTEMPT_CHARS, truncated),
        "INSTRUCTIONS": _cap("instructions", inputs.instructions.strip() or "None beyond the loop contract.",
                             INSTRUCTIONS_CHARS, truncated),
    }
    steering = ""
    if inputs.steering.strip():
        steering = f"\n## Notes from the developer\n\n{inputs.steering.strip()}\n"

    text = render(
        "unit_pack.md",
        UNIT_ID=unit.id,
        UNIT_TITLE=unit.title,
        ATTEMPT=str(unit.attempts + 1),
        PROGRESS=f"{done}/{len(units)} units done",
        BRANCH=inputs.branch or "(unknown)",
        HEAD=inputs.head[:12] or "(no commits)",
        CHECKS=format_checks(inputs.checks),
        STEERING=steering,
        PLAN_PATH=str(inputs.plan.path) if inputs.plan else "(no plan)",
        **sections,
    )
    return Pack(text=text, unit_id=unit.id, sections={k: len(v) for k, v in sections.items()},
                truncated=truncated, spec_pointers=pointers)
