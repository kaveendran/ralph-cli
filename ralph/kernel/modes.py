"""The other things `ralph start` can do besides the build loop:

- plan mode: one long session that writes the plan, then compiles it to units;
- task mode: one bounded iteration on work outside the plan (a bug, a chore);
- dry run: show what the next iteration would send, without running anything.
"""

import os
import tempfile
from pathlib import Path

from ralph import ui
from ralph.errors import RalphError
from ralph.fsutil import read_text, write_text_atomic
from ralph.graph.compile import compile_plan
from ralph.graph.schedule import why_nothing_is_ready
from ralph.context.pack import format_checks
from ralph.kernel.lock import RunLock
from ralph.kernel.loop import Kernel
from ralph.model.plan import parse_plan
from ralph.model.unit import Unit
from ralph.prompts import render, render_template
from ralph.runner.cli_backend import build_command, describe_command
from ralph.vcs import commit as vcs
from ralph.verify import gates as gate_runner
from ralph.verify.integrity import check_changes


def run_plan(kernel: Kernel, implementation_type: str = "feature") -> int:
    cr = kernel.cr
    if not cr.spec.exists():
        raise RalphError(f"{cr.spec} doesn't exist.", "Write the spec first; the plan is generated from it.")
    if cr.plan_prompt.exists():
        prompt = cr.plan_prompt.read_text(encoding="utf-8")
    else:
        prompt = render_template("spec-planning.md", SPEC_PATH=str(cr.spec.relative_to(kernel.root)),
                                 IMPLEMENTATION_TYPE=implementation_type,
                                 PLAN_PATH=str(cr.plan.relative_to(kernel.root)),
                                 EXISTING_CODE="true", SPECIAL_NOTES="(none)")
    with RunLock(kernel.project.lock):
        kernel.preflight(need_units=False)
        ui.section(f"Planning {cr.name} with {kernel.options.model or kernel.config.runner.model_plan}")
        run_dir = kernel.run_dir / "plan"
        call = kernel.agent_call(prompt, run_dir, "unit-result.json")
        call.json_schema, call.system_append = None, ""
        call.model = kernel.options.model or kernel.config.runner.model_plan
        call.max_turns = kernel.options.max_turns or 300
        call.max_budget_usd = kernel.options.max_budget_usd or 60
        call.timeout_seconds = 3 * 60 * 60
        result = kernel.run_agent(call)
        if not cr.plan.exists():
            raise RalphError(f"The planning session ended ({result.subtype or 'no result'}) without writing "
                             f"{cr.plan}.", f"See {run_dir / 'stream.jsonl'}.")
        return compile_and_commit(kernel, f"ralph: plan for {cr.name}")


def compile_and_commit(kernel: Kernel, message: str, allow_large: bool = False) -> int:
    cr, store = kernel.cr, kernel.store
    result = compile_plan(parse_plan(cr.plan), store.load() if store.exists() else {}, cr.name)
    for issue in result.issues:
        (ui.error if issue.severity == "reject" else ui.warn)(f"{issue.unit_id}: {issue.reason}")
    if result.rejected and not allow_large:
        ui.error(f"{len(result.rejected)} unit(s) are too big for one session, so nothing was written.",
                 "Split them in the plan and compile again, or pass --allow-large to accept them as they are.")
        return 1
    for issue in result.rejected:
        result.units[issue.unit_id].allow_large = True
    store.save_all(result.units.values())
    ui.ok(f"{len(result.units)} units in {cr.units_dir} ({len(result.added)} new).")
    if result.not_in_plan:
        ui.warn(f"Not in the plan any more (kept): {', '.join(result.not_in_plan)}")
    paths = [str(p.relative_to(kernel.root)) for p in [cr.plan, *(store.path(u) for u in result.units)]]
    sha = vcs.commit_paths(kernel.git, paths, message) if kernel.git.is_repo() else ""
    if sha:
        ui.ok(f"Committed the plan and units ({sha[:10]}).")
    return 0


def run_task(kernel: Kernel, title: str, text: str) -> int:
    """One bounded iteration on work outside the plan."""
    with RunLock(kernel.project.lock):
        kernel.preflight(need_units=False)
        prompt = render("task.md", TASK_TITLE=title, TASK=text.strip(),
                        INSTRUCTIONS=read_text(kernel.cr.instructions).strip() or "None beyond the loop contract.",
                        CHECKS=format_checks(kernel.checks_for(None)),
                        BRANCH=kernel.git.branch(), HEAD=kernel.git.head()[:12])
        run_dir = kernel.run_dir / "task"
        write_text_atomic(run_dir / "pack.md", prompt)
        task_unit = Unit(id="task", title=title, cr=kernel.cr.name)
        snapshot = vcs.take_snapshot(kernel.git)
        ui.section(f"Task · {title}")
        result = kernel.run_agent(kernel.agent_call(prompt, run_dir, "unit-result.json"))
        vcs.undo_agent_commits(kernel.git, snapshot)
        changes = vcs.run_changes(kernel.git, snapshot)
        report = result.structured or {}

        problems = []
        if result.failed or result.usage_limited:
            problems.append(f"the run failed ({result.subtype or 'no result'})")
        elif report.get("status") != "done":
            problems.append(f"the agent reported '{report.get('status', 'no result')}': {report.get('blocker', '')}")
        integrity = check_changes(vcs.changed_entries(kernel.git, changes.paths),
                                  vcs.diff_text(kernel.git, changes.paths), kernel.config.verify.frozen)
        problems += integrity.failures
        gate_results = []
        if not problems:
            gates = gate_runner.select_gates(kernel.config.verify, None)
            gate_results = gate_runner.run_gates(gates, kernel.root, run_dir / "gates.log",
                                                 kernel.config.verify.gate_timeout_minutes * 60)
            problems += [f"gate `{g.name}` failed (exit {g.exit_code})" for g in gate_results if not g.passed]

        commit = ""
        if not problems:
            commit = vcs.commit_paths(kernel.git, changes.paths,
                                      f"task: {title}\n\n{report.get('summary', '').strip()}\n\nGenerated-by: ralph (Claude Code)")
            ui.ok(f"Task done{' and committed (' + commit[:10] + ')' if commit else ' (nothing to commit)'}.")
        else:
            ui.warn("Task not accepted: " + "; ".join(problems) + ". Its changes are left in the working tree.")
        kernel.record(0, task_unit, "done" if not problems else "not_done", result, snapshot, run_dir,
                      commit=commit, changed=len(changes.paths), notes="; ".join(problems))
        return 0 if not problems else 1


def dry_run(kernel: Kernel) -> int:
    """What the next iteration would send. Writes nothing into the project."""
    units = kernel.store.load()
    unit = kernel.select(units)
    if unit is None:
        reasons = why_nothing_is_ready(units)
        ui.info("Nothing is ready to run:" if reasons else f"Every unit of {kernel.cr.name} is done.")
        for reason in reasons:
            ui.info(f"    {reason}")
        return 0
    steering = read_text(kernel.control.steer_file)     # peek; the real run consumes it
    pack = kernel.build_unit_pack(unit, units, steering=steering)
    with tempfile.TemporaryDirectory() as tmp:
        call = kernel.agent_call(pack.text, Path(tmp), "unit-result.json")
        command = describe_command(build_command(call))
    saved = Path(os.environ.get("TMPDIR", "/tmp")) / f"ralph-dry-run-{kernel.run_id}.md"
    saved.write_text(pack.text, encoding="utf-8")

    old_chars = sum(len(read_text(p)) for p in (kernel.cr.spec, kernel.cr.plan, kernel.cr.legacy_progress,
                                                 kernel.cr.legacy_build_prompt))
    ui.header(f"start — dry run ({kernel.cr.name})", "nothing is sent to Claude and nothing is written to the project")
    ui.info(f"  Unit:          {unit.id} {unit.title} (attempt {unit.attempts + 1})")
    ui.info(f"  Context pack:  {pack.chars:,} chars (~{pack.tokens:,} tokens), saved to {saved}")
    for name, chars in pack.sections.items():
        ui.info(f"      {name.lower().replace('_', ' '):<14} {chars:>7,} chars")
    if pack.truncated:
        ui.info(f"  Cut to fit:    {', '.join(pack.truncated)}")
    if pack.spec_pointers:
        ui.info(f"  Spec pointers: {len(pack.spec_pointers)} section(s) left for the agent to open on demand")
    ui.info(f"  For comparison: the spec, the whole plan, the old progress file and build prompt come to "
            f"{old_chars:,} chars (~{old_chars // 4:,} tokens).")
    ui.info(f"  Command:       {command}")
    return 0
