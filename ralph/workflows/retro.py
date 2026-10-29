"""`ralph retro`: look back at a CR once it's built.

Three analysts work in parallel: plan versus reality (from the unit table,
the iteration metrics and the git log), blocker patterns across CRs (from
the blocked and retried units' notes, and the lessons the agent recorded),
and test coverage (each finished unit's acceptance criteria against the test
files it changed). A lead writes the report, and its release-notes and
PR-description drafts are saved as files of their own.
"""

from pathlib import Path

from ralph.fsutil import read_text, write_text_atomic
from ralph.memory.learnings import group, read_inbox
from ralph.model.spec import acceptance_criteria, parse_spec
from ralph.model.store import UnitStore
from ralph.monitor.telemetry import read_records, unit_totals
from ralph.prompts import render
from ralph.team.ledger import read_local
from ralph.workflows import inputs
from ralph.workflows.council import TOOLS_NOTE, Council, Expert, reviews, total_cost
from ralph.workflows.critique import Outcome
from ralph.workflows.report import split_at
from ralph.workflows.workspace import Workspace

SECTIONS = ["## Summary", "## Plan vs Reality", "## Blocker Patterns", "## Test Coverage Gaps",
            "## Action Items Before Release", "## RELEASE_NOTES draft", "## PR Description draft"]
PRIOR_CHARS = 15_000


def _trouble(ws: Workspace, name: str) -> str:
    cr = ws.project.cr(name)
    store = UnitStore(cr.units_dir)
    if store.exists():
        return inputs.trouble(store.load(), store, name)
    return inputs.legacy_trouble(cr.legacy_progress) if cr.legacy_progress.exists() else "(no records)"


def _lessons(ws: Workspace) -> str:
    groups = group(read_inbox(ws.project.learnings_inbox))[:15]
    return "\n".join(f"- {g.rule} ({', '.join(g.units) or '?'}; {g.status})" for g in groups) or "(none recorded)"


def retro(ws: Workspace, council: Council, write_files: bool = True) -> tuple[Outcome, list[Path]]:
    units, plan = ws.units, ws.plan
    plan_text = inputs.plan_text(plan, limit=30_000) if plan else "(no plan file)"
    totals = unit_totals(read_records(ws.project.iterations_log), ws.cr.name)
    table = inputs.unit_table(units, totals) if units else inputs.legacy_trouble(ws.cr.legacy_progress, limit=20_000)
    prior = "\n\n".join(f"## {name}\n{_trouble(ws, name)}" for name in ws.project.cr_names() if name != ws.cr.name)
    ledger_files = {e.unit: e.paths for e in read_local(ws.git) if e.cr == ws.cr.name}

    experts = [
        Expert("plan-vs-reality", render("retro/plan-vs-reality.md", TOOLS_NOTE=TOOLS_NOTE, UNITS=table,
                                         PLAN=plan_text, GIT_LOG=inputs.git_log(ws.git))),
        Expert("blockers", render("retro/blockers.md", TROUBLE=_trouble(ws, ws.cr.name),
                                  PRIOR=prior[:PRIOR_CHARS] or "(no earlier CRs)", LESSONS=_lessons(ws)), tools=[]),
        Expert("coverage", render("retro/coverage.md", TOOLS_NOTE=TOOLS_NOTE, PLAN=plan_text,
                                  CRITERIA=acceptance_criteria(parse_spec(ws.cr.spec))[:8_000] or "(none found)",
                                  TESTS=inputs.unit_tests(ws.git, units, ledger_files))),
    ]
    opinions = council.ask(experts)
    values = reviews(opinions, {"plan-vs-reality": "PLAN_VS_REALITY", "blockers": "BLOCKERS", "coverage": "COVERAGE"})
    lead = council.lead("lead", render("retro/lead.md", CR=ws.cr.name, **values))

    folder = ws.cr.dir / "retro"
    path = ws.report_path(folder, "retro")
    write_text_atomic(path, lead.text + "\n")
    written = []
    if write_files:
        parts = split_at(lead.text, SECTIONS)
        for heading, filename in (("## RELEASE_NOTES draft", "RELEASE_NOTES.md"),
                                  ("## PR Description draft", "PR_DESCRIPTION.md")):
            if parts.get(heading):
                write_text_atomic(folder / filename, parts[heading] + "\n")
                written.append(folder / filename)
    return Outcome(path, lead.text, total_cost(opinions, lead)), written
