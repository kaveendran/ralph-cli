"""`ralph critique` and `ralph refine-plan`: review a plan before the build starts.

Eight experts read the plan (architect, tester, risk analyst, a domain expert
for the stack in use, infrastructure, security, performance, docs); a lead
merges their findings into one report with CRITICAL, IMPORTANT and SUGGESTED
items, saved under `development/<cr>/critique/`. The infrastructure expert
runs only when the project has a docker-compose.test.yml to review.

refine-plan turns a report into proposed plan edits and asks you about each
revision (workflows/refine.py). The edits come back per unit and are spliced
in, so units it doesn't touch stay exactly as they were.
"""

import re
from dataclasses import dataclass
from pathlib import Path

from ralph.fsutil import read_text, write_text_atomic
from ralph.model.plan import UNIT_TOKEN
from ralph.model.spec import acceptance_criteria, parse_spec
from ralph.prompts import render
from ralph.skills.deps import installed_versions
from ralph.workflows import inputs
from ralph.workflows.council import TOOLS_NOTE, Council, Expert, reviews, total_cost
from ralph.workflows.plan_edit import apply_edits
from ralph.workflows.refine import Refinement, refine
from ralph.workflows.workspace import Workspace

EXPERTS = ("architect", "tester", "risk", "domain", "infrastructure", "security", "performance", "docs")
BACKEND = {"fastapi", "django", "flask", "starlette", "sqlalchemy", "pydantic", "pytest", "alembic", "express",
           "@nestjs/core", "fastify", "prisma", "rails", "spring-boot"}
FRONTEND = {"react", "vue", "svelte", "@angular/core", "vite", "next", "nuxt", "vitest", "@testing-library/react"}
TYPE_FOCUS = {"backend+frontend": "fullstack", "backend": "backend", "api": "backend", "database": "backend",
              "frontend": "frontend"}
CITED_CHARS = 60_000
SPEC_CHARS = 8_000


@dataclass
class Outcome:
    path: Path
    text: str
    cost_usd: float


def stack_focus(root: Path, impl_type: str = "") -> tuple[str, str]:
    """(which focus list the domain expert gets, the stack in words), from the dependencies in use."""
    names = set(installed_versions(root))
    backend, frontend = sorted(names & BACKEND), sorted(names & FRONTEND)
    if impl_type:
        focus = TYPE_FOCUS.get(impl_type, "feature")
    else:
        focus = "fullstack" if backend and frontend else "backend" if backend else "frontend" if frontend else "feature"
    return focus, ", ".join(backend + frontend) or "not detected from the project's dependencies"


def critique(ws: Workspace, council: Council, impl_type: str = "") -> Outcome:
    plan = ws.require_plan()
    criteria = acceptance_criteria(parse_spec(ws.cr.spec))[:SPEC_CHARS] or "(the spec lists none)"
    common = {"TOOLS_NOTE": TOOLS_NOTE, "PLAN": inputs.plan_text(plan), "CRITERIA": criteria,
              "SPEC_PATH": str(ws.cr.spec.relative_to(ws.root))}
    focus, stack = stack_focus(ws.root, impl_type)
    experts = [Expert(name, render(f"critique/{name}.md", **common))
               for name in ("architect", "tester", "risk", "security", "performance", "docs")]
    experts.append(Expert("domain", render("critique/domain.md", STACK=stack,
                                           FOCUS=render(f"critique/focus-{focus}.md").strip(), **common)))
    compose = ws.root / "docker-compose.test.yml"
    if compose.exists():
        experts.append(Expert("infrastructure", render("critique/infrastructure.md", TOOLS_NOTE=TOOLS_NOTE,
                                                       COMPOSE=read_text(compose)[:30_000])))

    opinions = council.ask(experts)
    values = reviews(opinions, {name: name.upper() for name in EXPERTS})
    if not compose.exists():
        values["INFRASTRUCTURE"] = "(The project has no docker-compose.test.yml, so there's no infrastructure review.)"
    lead = council.lead("lead", render("critique/lead.md", CR=ws.cr.name, **values))
    path = ws.report_path(ws.cr.dir / "critique", "report")
    write_text_atomic(path, lead.text + "\n")
    return Outcome(path, lead.text, total_cost(opinions, lead))


def latest_report(folder: Path) -> Path | None:
    reports = sorted(folder.glob("*-report.md"), key=lambda p: p.stat().st_mtime)
    return reports[-1] if reports else None


def propose_plan_edits(ws: Workspace, council: Council, report: str, scope: str) -> str:
    plan = ws.require_plan()
    ids = [u.id for u in plan.units]
    cited = [unit_id for unit_id in dict.fromkeys(UNIT_TOKEN.findall(report)) if unit_id in ids]
    sections = "\n\n".join(plan.unit(unit_id).section for unit_id in cited)
    prompt = render("refine/plan.md", SCOPE=scope, LAST_ID=max(ids, key=lambda i: int(re.sub(r"\D", "", i) or 0),
                                                              default="U000"),
                    PLAN_PATH=str(plan.path), REPORT=report, PLAN=inputs.plan_text(plan, limit=30_000),
                    CITED_UNITS=sections[:CITED_CHARS] or "(the critique cites no units)")
    edits, _ = council.structured("plan-edits", prompt, "plan-edits.json")
    return apply_edits(read_text(plan.path), edits)


def refine_plan(ws: Workspace, council: Council, max_iterations: int, gate: str, scope: str,
                impl_type: str = "") -> int:
    plan = ws.require_plan()
    folder = ws.cr.dir / "critique"
    return refine(Refinement(
        target=plan.path, revisions_dir=plan.path.parent / "revisions",
        critique=lambda: critique(ws, council, impl_type).path,
        latest_report=lambda: latest_report(folder),
        propose=lambda report: propose_plan_edits(ws, council, report, scope),
        max_iterations=max_iterations, gate=gate))
