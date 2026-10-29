"""`ralph deploy scaffold | critique | refine`: prepare a CR's deployment.

- scaffold drafts four documents from the plan (deploy plan, runbook,
  rollback, verification) in parallel. Claude fills in what the plan
  determines and leaves every environment detail as a `<TODO: ...>` marker:
  structure is automated, facts about your environment never are.
- critique has six experts (migration, feature flags, rollout,
  observability, rollback, compliance) review them, then a peer-review pass
  finds where they agree and disagree, and a lead writes the verdict.
- refine is the human-gated critique → edit loop over deploy-plan.md.
"""

import re
from pathlib import Path

from ralph.errors import RalphError
from ralph.fsutil import read_text, write_text_atomic
from ralph.model.plan import UNIT_TOKEN
from ralph.model.spec import acceptance_criteria, parse_spec
from ralph.prompts import render
from ralph.workflows import inputs
from ralph.workflows.council import TOOLS_NOTE, Council, Expert, reviews, total_cost
from ralph.workflows.critique import Outcome, latest_report
from ralph.workflows.refine import Refinement, refine
from ralph.workflows.report import todo_count
from ralph.workflows.workspace import Workspace

DOCUMENTS = {"deploy-plan.md": "scaffold-plan", "runbook.md": "scaffold-runbook",
             "rollback.md": "scaffold-rollback", "verify.md": "scaffold-verify"}
EXPERTS = {"migration": "MIGRATION", "feature-flags": "FEATURE_FLAGS", "rollout": "ROLLOUT",
           "observability": "OBSERVABILITY", "rollback": "ROLLBACK", "compliance": "COMPLIANCE"}
DOCUMENT_CHARS = 30_000


def deploy_dir(ws: Workspace) -> Path:
    return ws.cr.dir / "deploy"


def scaffold(ws: Workspace, council: Council, force: bool = False) -> tuple[list[Path], int, float]:
    """(the documents written, how many <TODO> markers they hold, cost)."""
    plan = ws.require_plan()
    folder = deploy_dir(ws)
    existing = [name for name in DOCUMENTS if (folder / name).exists()]
    if existing and not force:
        raise RalphError(f"{', '.join(existing)} already exist in {folder}.", "Pass --force to draft them again.")
    common = {"RULES": render("deploy/rules.md").strip(), "CR": ws.cr.name, "TOOLS_NOTE": TOOLS_NOTE,
              "UNITS": inputs.unit_table(ws.units) if ws.units else "(no units yet)",
              "PLAN": inputs.plan_text(plan, limit=40_000)}
    extra = {"scaffold-rollback": {"ROLLBACKS": inputs.rollback_sections(plan)},
             "scaffold-verify": {"CRITERIA": acceptance_criteria(parse_spec(ws.cr.spec))[:8_000] or "(none found)"}}
    experts = [Expert(name, render(f"deploy/{prompt}.md", **common, **extra.get(prompt, {})),
                      schema="document.json", result_key="document") for name, prompt in DOCUMENTS.items()]
    opinions = council.ask(experts)
    written = []
    for name, opinion in opinions.items():
        if opinion.text:
            write_text_atomic(folder / name, opinion.text + "\n")
            written.append(folder / name)
    return written, todo_count([read_text(p) for p in written]), total_cost(opinions)


def _bundle(ws: Workspace) -> str:
    folder = deploy_dir(ws)
    parts = [f"--- {name.upper().replace('.MD', '').replace('-', ' ')} ---\n"
             f"{read_text(folder / name)[:DOCUMENT_CHARS] or '(missing)'}" for name in DOCUMENTS]
    plan = ws.plan
    parts.append(f"--- UNITS (for cross-reference) ---\n{inputs.unit_table(ws.units) if ws.units else '(no units)'}")
    parts.append(f"--- SOURCE PLAN (for cross-reference) ---\n{inputs.plan_text(plan, limit=30_000) if plan else '(no plan)'}")
    return "\n\n".join(parts)


def critique(ws: Workspace, council: Council, peer_review: bool = True) -> Outcome:
    folder = deploy_dir(ws)
    if not (folder / "deploy-plan.md").exists():
        raise RalphError(f"There's no {folder / 'deploy-plan.md'}.", f"Run 'ralph deploy scaffold --cr {ws.cr.name}' first.")
    bundle = _bundle(ws)
    opinions = council.ask([Expert(name, render(f"deploy/{name}.md", TOOLS_NOTE=TOOLS_NOTE, BUNDLE=bundle))
                            for name in EXPERTS])
    values = reviews(opinions, EXPERTS)
    peer = council.lead("peer-review", render("deploy/peer-review.md", **values)) if peer_review else None
    todos = todo_count([read_text(folder / name) for name in DOCUMENTS])
    lead = council.lead("lead", render("deploy/lead.md", CR=ws.cr.name, TODOS=str(todos),
                                       PEER_REVIEW=peer.text if peer else "(skipped)", **values))
    path = ws.report_path(folder / "critique", "report")
    write_text_atomic(path, lead.text + "\n")
    return Outcome(path, lead.text, total_cost(opinions, lead, *([peer] if peer else [])))


def check_revision(before: str, after: str) -> None:
    """Reject a revised deploy plan that lost units or sections, or came back truncated."""
    lost_units = set(UNIT_TOKEN.findall(before)) - set(UNIT_TOKEN.findall(after))
    headings = lambda text: {line.strip() for line in text.splitlines() if re.match(r"^##\s", line)}
    lost_sections = headings(before) - headings(after)
    if lost_units:
        raise RalphError(f"The revision drops {', '.join(sorted(lost_units))}.")
    if lost_sections:
        raise RalphError(f"The revision drops the section(s) {', '.join(sorted(lost_sections))}.")
    if len(after) < len(before) * 0.6:
        raise RalphError(f"The revision is {len(after):,} characters against {len(before):,}: it looks truncated.")


def propose_revision(ws: Workspace, council: Council, report: str, scope: str) -> str:
    target = deploy_dir(ws) / "deploy-plan.md"
    before = read_text(target)
    result, _ = council.structured("deploy-edits", render("refine/deploy.md", SCOPE=scope, REPORT=report,
                                                          DOCUMENT=before), "document.json")
    after = str(result.get("document", "")).strip() + "\n"
    check_revision(before, after)
    return after


def refine_deploy(ws: Workspace, council: Council, max_iterations: int, gate: str, scope: str) -> int:
    folder = deploy_dir(ws)
    target = folder / "deploy-plan.md"
    if not target.exists():
        raise RalphError(f"There's no {target}.", f"Run 'ralph deploy scaffold --cr {ws.cr.name}' first.")
    return refine(Refinement(
        target=target, revisions_dir=folder / "revisions",
        critique=lambda: critique(ws, council, peer_review=False).path,
        latest_report=lambda: latest_report(folder / "critique"),
        propose=lambda report: propose_revision(ws, council, report, scope),
        max_iterations=max_iterations, gate=gate))
