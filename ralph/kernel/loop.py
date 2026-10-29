"""The loop kernel: one bounded, verified unit per iteration.

    preflight → select → impact check → prepare → run → verify → commit → record → decide

Each phase is one method below, in that order. The kernel never fetches,
pushes or rebases; syncing with teammates is a manual command
(docs/meta-harness-plan.md §11).
"""

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ralph import ui
from ralph.config import Config
from ralph.context.pack import Pack, PackInputs, build_pack
from ralph.errors import RalphError
from ralph.fsutil import read_text, write_text_atomic
from ralph.graph.compile import discovered_unit_id
from ralph.graph.schedule import next_unit, why_nothing_is_ready
from ralph.kernel.control import Control
from ralph.kernel.lock import RunLock
from ralph.kernel.outcome import Evidence, Judgement, Verdict, judge
from ralph.memory.learnings import read_inbox
from ralph.memory.learnings import relevant as relevant_lessons
from ralph.memory.lint import lint_memory
from ralph.model.plan import Plan, parse_plan
from ralph.model.spec import Spec, parse_spec
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.monitor.notify import Notifier
from ralph.monitor.signals import SignalMonitor
from ralph.monitor.telemetry import IterationRecord, append_record, now_iso
from ralph.paths import ProjectPaths
from ralph.prompts import render, schema
from ralph.runner import compat
from ralph.runner.base import AgentCall, AgentResult, Runner
from ralph.runner.settings import agent_env, project_mcp_config, write_agents_file, write_run_settings
from ralph.runner.stream import describe_tool_use
from ralph.skills.deps import installed_versions
from ralph.skills.lint import lint_skills
from ralph.skills.refresh import stale_skills
from ralph.skills.registry import discover as discover_skills
from ralph.skills.select import suggest as suggest_skills
from ralph.team import interfaces, ledger
from ralph.team.impact import ImpactReport, age, assess, team_view
from ralph.vcs import commit as vcs
from ralph.vcs.commit import RunChanges, Snapshot
from ralph.vcs.git import Git
from ralph.verify import gates as gate_runner
from ralph.verify.integrity import check_changes
from ralph.verify.review import REVIEW_TOOLS, review_prompt, run_review, should_review

# pytest and unittest exit 5 when they find no tests: normal before the first
# unit of a new project (it only excuses the baseline, never a unit's gates).
NO_TESTS_COLLECTED = 5


@dataclass
class StartOptions:
    cr: str
    max_iterations: int = 0              # 0: until everything is done or a stop rule fires
    model: str = ""
    max_turns: int | None = None
    max_budget_usd: float | None = None
    only_units: list[str] = field(default_factory=list)
    allow_main: bool = False
    skip_baseline: bool = False
    gates_advisory: bool = False
    allow_version_drift: bool = False
    review: str | None = None            # overrides verify.review for this run


class Kernel:
    def __init__(self, project: ProjectPaths, config: Config, runner: Runner, options: StartOptions):
        self.project, self.config, self.runner, self.options = project, config, runner, options
        self.cr = project.cr(options.cr)
        self.root = project.root
        self.git = Git(project.root)
        self.store = UnitStore(self.cr.units_dir)
        self.control = Control(project.control_dir)
        self.run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.run_dir = project.log_dir / self.run_id
        self.developer = self.git.run("config", "user.name", check=False).stdout.strip()
        self.email = self.git.run("config", "user.email", check=False).stdout.strip()
        self.failures = self.invalid_results = self.stalls = 0
        self.converged_once = False
        self._plan_cache: tuple[float, Plan | None] | None = None
        self._spec_cache: dict[str, Spec | None] = {}
        self._skill_cache = None
        self.notifier = Notifier(config.notify, project.root.name)

    # ── The loop ─────────────────────────────────────────────────────────────

    def run(self) -> int:
        with RunLock(self.project.lock):
            self.preflight()
            iteration = 0
            while not (self.options.max_iterations and iteration >= self.options.max_iterations):
                self.control.wait_while_paused(
                    on_wait=lambda: ui.info("  Paused after the last iteration. Run 'ralph resume' to continue."))
                units = self.store.load()
                unit = self.select(units)
                if unit is None:
                    if self.finish(units):
                        continue          # the convergence check added units
                    break
                stop = self.iterate(iteration, unit, units)
                iteration += 1
                if stop:
                    break
            if self.options.max_iterations and iteration >= self.options.max_iterations:
                ui.info(f"\n  Reached --max {self.options.max_iterations}.")
        return 0

    def iterate(self, iteration: int, unit: Unit, units: dict[str, Unit]) -> bool:
        """Run one unit through every phase. Returns True when the loop should stop."""
        impacts = self.impact_check(unit)
        if self._team_stop(unit, impacts):
            return True
        run_dir = self.run_dir / f"{iteration:02d}-{unit.id}"
        call, pack = self.prepare(unit, units, run_dir, impacts)
        ui.section(f"Iteration {iteration} · {unit.id} {unit.title} · attempt {unit.attempts + 1} "
                   f"· context pack ~{pack.tokens:,} tokens")
        snapshot = vcs.take_snapshot(self.git)
        try:
            result = self.run_agent(call)
        except KeyboardInterrupt:
            self._keep_wip(unit, snapshot)
            raise
        return self.settle(iteration, unit, units, result, snapshot, run_dir)

    def _keep_wip(self, unit: Unit, snapshot: Snapshot) -> RunChanges:
        """Remember what an unfinished run changed, so the unit's eventual commit includes it."""
        vcs.undo_agent_commits(self.git, snapshot)
        changes = vcs.run_changes(self.git, snapshot, own_paths=unit.wip_paths)
        unit.wip_paths = sorted(set(unit.wip_paths) | set(changes.paths))
        unit.wip_overlap = sorted(set(unit.wip_overlap) | set(changes.overlapping))
        if changes.paths and unit.status is Status.TODO:
            unit.status = Status.IN_PROGRESS
        self.store.save(unit)
        return changes

    # ── 1. Preflight ─────────────────────────────────────────────────────────

    def preflight(self, need_units: bool = True) -> None:
        runner_config = self.config.runner
        if not self.git.is_repo():
            raise RalphError(f"{self.root} isn't a git repository.", "Run 'git init' first.")
        if runner_config.permissions != "bypass":
            raise RalphError(f"runner.permissions = '{runner_config.permissions}' isn't supported yet.",
                             "Use 'bypass' (with guardrail hooks), ideally inside a container.")
        compat.find_claude()
        version = compat.check_version(runner_config.claude_version, allow_drift=self.options.allow_version_drift)
        self._check_branch()
        if not self.git.user_configured():
            raise RalphError("git has no user.email set, so the harness can't commit.",
                             "Set it with: git config user.name 'Your Name' && git config user.email you@example.com")
        if need_units and not self.store.exists():
            raise RalphError(f"{self.cr.name} has no units yet ({self.cr.units_dir}).",
                             f"Run 'ralph start --mode plan --cr {self.cr.name}' to write a plan, or "
                             f"'ralph graph compile --cr {self.cr.name}' if the plan exists. For a project from "
                             f"the old loop, run 'ralph migrate --cr {self.cr.name}'.")
        ui.header(f"start — {self.cr.name}",
                  f"claude {version} · branch {self.git.branch()} · run {self.run_id} · logs in {self.run_dir}")
        ui.info(f"  Per iteration: max {self.options.max_turns or runner_config.max_turns} turns, "
                f"max ${self.options.max_budget_usd or runner_config.max_budget_usd:g}, "
                f"{runner_config.timeout_minutes} min, compaction at {runner_config.compact_window:,} tokens")
        ui.info(f"  Hermetic: {'on' if runner_config.hermetic else 'off'} · pushes: never (sync is manual)")
        self._team_status()
        self._memory_and_skill_checks()
        if not self.config.verify.gates:
            ui.warn("No gates are configured in .ralph/config.toml, so nothing checks the agent's work "
                    "except the review. Add [verify.gates].")
        elif not self.options.skip_baseline:
            self._baseline()

    def _team_status(self) -> None:
        """Where teammates' changes come from, and how old that knowledge is. Never fetches."""
        view = team_view(self.git, self.config.team, self.email)
        if view.shared is None:
            return
        fetched = age(view.last_fetch)
        ui.info(f"  Team: shared branch {view.shared.name} · last fetch {fetched} · "
                f"{len(view.entries)} recent change(s) from teammates")
        stale = self.config.team.stale_sync_hours
        if view.last_fetch is None or (datetime.now(timezone.utc) - view.last_fetch).total_seconds() > stale * 3600:
            ui.warn(f"Your last fetch was {fetched}, so teammates' newer work isn't considered. "
                    "Run 'ralph sync' between runs (the loop itself never fetches).")
        if view.conflicts:
            ui.warn(f"Your branch conflicts with {view.shared.name} in {', '.join(view.conflicts[:5])}. "
                    "Units that touch those files will stop; run 'ralph sync' to resolve.")

    def _memory_and_skill_checks(self) -> None:
        """Warn (never stop) about memory over budget, broken skills and skills gone stale."""
        findings = lint_memory(self.root, self.config.memory, [self.cr.units_dir], self.project.learnings_inbox)
        findings += [f for f in lint_skills(self._skills(), self.config.memory.skill_lines) if f.severity == "error"]
        for finding in findings[:6]:
            ui.warn(f"{finding.path.relative_to(self.root) if finding.path.is_absolute() else finding.path}: "
                    f"{finding.message}")
        if len(findings) > 6:
            ui.warn(f"…and {len(findings) - 6} more; run 'ralph memory lint' and 'ralph skills lint'.")
        for stale in stale_skills(self._skills(), installed_versions(self.root)):
            ui.warn(f"Skill '{stale.item}' was written for {stale.package} {stale.written_for}, but "
                    f"{stale.source} has {stale.installed}. Run 'ralph skills refresh'.")

    def _check_branch(self) -> None:
        if self.options.allow_main:
            return
        branch = self.git.branch()
        if not branch:
            raise RalphError("HEAD is detached, so commits would be hard to find.",
                             f"Switch to a branch first: git switch -c ralph/{self.cr.name}")
        protected = self.config.loop.protect_branches or [b for b in [self.git.default_branch()] if b]
        if branch in protected:
            raise RalphError(f"You're on '{branch}', which the loop doesn't commit to.",
                             f"Create a branch for the work: git switch -c ralph/{self.cr.name} "
                             "(or pass --allow-main).")

    def _baseline(self) -> None:
        """Run the default gates once, so a tree that is already red doesn't burn units."""
        gates = gate_runner.select_gates(self.config.verify, None)
        if not gates:
            return
        ui.info(f"  Baseline: running {', '.join(n for n, _ in gates)} once before the first unit…")
        results = gate_runner.run_gates(gates, self.root, self.run_dir / "baseline-gates.log",
                                        self.config.verify.gate_timeout_minutes * 60)
        failed = next((r for r in results if not r.passed), None)
        if failed is not None and failed.exit_code == NO_TESTS_COLLECTED:
            ui.info(f"  Baseline: '{failed.name}' found no tests yet (exit 5), so there's nothing to break.")
            return
        if failed is None:
            ui.ok("Baseline gates pass.")
        elif self.options.gates_advisory:
            ui.warn(f"Baseline gate '{failed.name}' fails; continuing because of --gates-advisory.")
        else:
            raise RalphError(f"The tests already fail before any unit runs (gate '{failed.name}', exit "
                             f"{failed.exit_code}). Log: {failed.log_path}",
                             "Fix the tree first, or pass --gates-advisory to run anyway, or --skip-baseline.")

    # ── 2. Select ────────────────────────────────────────────────────────────

    def select(self, units: dict[str, Unit]) -> Unit | None:
        only = set(self.options.only_units) or None
        return next_unit(units, only)

    # ── 3. Impact check ──────────────────────────────────────────────────────

    def impact_check(self, unit: Unit) -> ImpactReport:
        """Teammates' changes that affect this unit, from the ledger and the
        shared branch as your last `ralph sync` left them. Reads local data only."""
        return assess(team_view(self.git, self.config.team, self.email), unit, self._unit_text(unit), self.root)

    def _team_stop(self, unit: Unit, impacts: ImpactReport) -> bool:
        """Stop when a teammate already did this unit, or its files won't merge with theirs."""
        if impacts.duplicates:
            reason = f"{impacts.duplicates[0].detail}, so building it here would duplicate their work"
            hint = "Run 'ralph sync' to pull their work, then start again."
        elif impacts.conflicts_here:
            reason = (f"{unit.id}'s files conflict with teammates' work on the shared branch: "
                      f"{', '.join(impacts.conflicts_here[:5])}")
            hint = "Run 'ralph sync', resolve the conflicts, then start again."
        else:
            return False
        ui.error("Stopping: " + reason, hint)
        self.notifier.send("stopped", reason, cr=self.cr.name, unit=unit.id)
        return True

    def _unit_text(self, unit: Unit) -> str:
        plan = self._plan()
        plan_unit = plan.unit(unit.id) if plan else None
        return plan_unit.section if plan_unit else unit.description

    # ── 4. Prepare ───────────────────────────────────────────────────────────

    def prepare(self, unit: Unit, units: dict[str, Unit], run_dir: Path,
                impacts: ImpactReport | None = None) -> tuple[AgentCall, Pack]:
        pack = self.build_unit_pack(unit, units, steering=self.control.take_steer(),
                                    team_changes=impacts.digest() if impacts else "")
        write_text_atomic(run_dir / "pack.md", pack.text)
        return self.agent_call(pack.text, run_dir, "unit-result.json"), pack

    def build_unit_pack(self, unit: Unit, units: dict[str, Unit], steering: str = "",
                        team_changes: str = "") -> Pack:
        lessons = relevant_lessons(read_inbox(self.project.learnings_inbox), unit.key_files,
                                   self.config.memory.learnings_in_pack)
        skills = suggest_skills(self._skills(), unit, self._unit_text(unit))
        return build_pack(PackInputs(
            unit=unit, units=units, plan=self._plan(), specs=self._specs(unit),
            instructions=read_text(self.cr.instructions), last_attempt=self.store.recent_notes(unit.id),
            steering=steering, team_changes=team_changes, checks=self.checks_for(unit),
            branch=self.git.branch(), head=self.git.head(),
            lessons=[_lesson_line(lesson) for lesson in lessons],
            skills=[(skill.name, why) for skill, why in skills]))

    def _skills(self):
        if self._skill_cache is None:
            self._skill_cache = discover_skills(self.root)
        return self._skill_cache

    def checks_for(self, unit: Unit | None) -> list[tuple[str, str]]:
        """The gates a unit must pass, plus any `ralph steer --hard` checks."""
        gates = gate_runner.select_gates(self.config.verify, unit)
        return gates + [(f"steer-check-{n}", command) for n, command in enumerate(self.control.checks(), start=1)]

    def agent_call(self, prompt: str, run_dir: Path, schema_name: str) -> AgentCall:
        runner_config = self.config.runner
        return AgentCall(
            prompt=prompt, cwd=self.root, model=self.options.model or runner_config.model_build,
            log_path=run_dir / "stream.jsonl",
            max_turns=self.options.max_turns or runner_config.max_turns,
            max_budget_usd=self.options.max_budget_usd or runner_config.max_budget_usd,
            timeout_seconds=runner_config.timeout_minutes * 60,
            silence_seconds=runner_config.silence_minutes * 60,
            system_append=render("contract.md"), json_schema=schema(schema_name),
            skip_permissions=True,
            settings_file=write_run_settings(self.root, run_dir, self.config.verify.frozen),
            agents_file=write_agents_file(run_dir),
            hermetic=runner_config.hermetic,
            mcp_config=project_mcp_config(self.root, run_dir) if runner_config.hermetic else None,
            setting_sources=runner_config.setting_sources,
            env=agent_env(self.root, runner_config.compact_window, runner_config.hermetic),
        )

    def _plan(self) -> Plan | None:
        path = self.cr.plan
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        if self._plan_cache is None or self._plan_cache[0] != mtime:
            self._plan_cache = (mtime, parse_plan(path))
        return self._plan_cache[1]

    def _specs(self, unit: Unit) -> dict[str, Spec | None]:
        names = {self.cr.name} | {ref.split(":")[0] for ref in unit.spec_refs if ":" in ref}
        for name in names:
            if name not in self._spec_cache:
                self._spec_cache[name] = parse_spec(self.project.cr(name).spec)
        return {name: self._spec_cache[name] for name in names}

    # ── 5. Run ───────────────────────────────────────────────────────────────

    def run_agent(self, call: AgentCall) -> AgentResult:
        monitor = SignalMonitor()

        prefix = f"{self.root}/"

        def on_event(event: dict, stats) -> str | None:
            for line in describe_tool_use(event):
                ui.info(f"    {line.replace(prefix, '')}")
            alerts, stop = monitor.check(stats)
            for alert in alerts:
                ui.warn(alert)
            return stop

        try:
            return self.runner.run(call, on_event)
        except KeyboardInterrupt:
            ui.warn("Interrupted. The unit stays as it was; its work is still in the tree. "
                    "Run 'ralph start' to carry on.")
            raise

    # ── 6–9. Settle: verify, commit, record, decide ──────────────────────────

    def settle(self, iteration: int, unit: Unit, units: dict[str, Unit], result: AgentResult,
               snapshot: Snapshot, run_dir: Path) -> bool:
        if result.usage_limited:
            self._keep_wip(unit, snapshot)
            self.record(iteration, unit, "usage_limit", result, snapshot, run_dir)
            ui.warn("The Claude usage limit was reached. Stopping; run 'ralph start' again after it resets.")
            self.notifier.send("usage_limit", f"the usage limit was reached during {unit.id}; the loop stopped",
                               cr=self.cr.name, unit=unit.id)
            return True
        if result.failed:
            self._keep_wip(unit, snapshot)
            return self._agent_failed(iteration, unit, result, snapshot, run_dir)
        self.failures = 0

        undone = vcs.undo_agent_commits(self.git, snapshot)
        if undone:
            ui.warn(f"The agent made {undone} commit(s) despite the contract; undone (its changes are kept "
                    "for the harness's checks).")
        changes = vcs.run_changes(self.git, snapshot, own_paths=unit.wip_paths)
        if changes.overlapping:
            ui.warn(f"The agent also changed files you had uncommitted changes in: {', '.join(changes.overlapping[:5])}")
        report = self._valid_report(unit, result)

        evidence = self.verify(unit, report, result, changes, run_dir)
        judgement = judge(evidence, unit.attempts, self.config.verify.max_attempts)
        commit = self.apply(unit, units, report, judgement, evidence, changes)
        self.record(iteration, unit, judgement.verdict.value, result, snapshot, run_dir,
                    evidence=evidence, commit=commit, changed=len(changes.paths), notes=judgement.reason)
        # A capped or harness-stopped run has no result by design; only count real omissions.
        expected_result = not (result.capped or result.stopped_by)
        return self.decide(judgement, report is not None or not expected_result, changes)

    def _agent_failed(self, iteration, unit, result, snapshot, run_dir) -> bool:
        self.failures += 1
        self.record(iteration, unit, "failed", result, snapshot, run_dir,
                    notes=f"exit {result.exit_code}, {result.subtype or 'no result'}")
        limit = self.config.loop.max_agent_failures
        ui.warn(f"claude failed (exit {result.exit_code}, {result.subtype or 'no result event'}); "
                f"{self.failures} of {limit} in a row. Log: {result_log(run_dir)}")
        if self.failures >= limit:
            ui.error(f"Stopping: claude failed {limit} times in a row.", "Check the last log, then run 'ralph start' again.")
            self.notifier.send("stopped", f"claude failed {limit} times in a row", cr=self.cr.name, unit=unit.id)
            return True
        delay = self.config.loop.retry_delay_seconds * 2 ** (self.failures - 1)
        ui.info(f"  Retrying in {delay}s…")
        time.sleep(delay)
        return False

    def _valid_report(self, unit: Unit, result: AgentResult) -> dict | None:
        report = result.structured
        if report and report.get("unit") not in (unit.id, unit.id.split("-")[0]):
            ui.warn(f"The agent reported on {report.get('unit')}, not {unit.id}; ignoring its result.")
            return None
        return report

    # 6. Verify

    def verify(self, unit: Unit, report: dict | None, result: AgentResult, changes: RunChanges,
               run_dir: Path) -> Evidence:
        evidence = Evidence(
            reported=report.get("status") if report else None,
            blocker=(report or {}).get("blocker", ""),
            capped=result.capped,
            stopped_by="" if result.stopped_by == "timeout" else result.stopped_by,
            previous_review_gaps=unit.last_review_gaps,
        )
        if evidence.reported != "done" or evidence.capped or evidence.stopped_by:
            return evidence

        paths = sorted(set(unit.wip_paths) | set(changes.paths))
        evidence.integrity = check_changes(vcs.changed_entries(self.git, paths), vcs.diff_text(self.git, paths),
                                           self.config.verify.frozen)
        if not evidence.integrity.passed:
            return evidence

        gates = self.checks_for(unit)
        if gates:
            ui.info(f"  Gates: {', '.join(n for n, _ in gates)}")
            evidence.gates = gate_runner.run_gates(gates, self.root, run_dir / "gates.log",
                                                   self.config.verify.gate_timeout_minutes * 60)
        if not all(g.passed for g in evidence.gates):
            return evidence

        mode = self.options.review or self.config.verify.review
        if should_review(mode, unit.priority, unit.size):
            ui.info("  Review: a read-only reviewer is checking the tests against the acceptance criteria…")
            plan = self._plan()
            plan_unit = plan.unit(unit.id) if plan else None
            prompt = review_prompt(
                unit_section=plan_unit.section if plan_unit else unit.description or unit.title,
                rules=read_text(self.cr.instructions), changed_files=paths,
                diff=vcs.diff_text(self.git, paths), integrity_flags=evidence.integrity.flags)
            evidence.review = run_review(self.runner, self.analysis_call(prompt, run_dir / "review",
                                                                         "review-result.json", max_turns=40))
            if evidence.review.verdict == "unavailable":
                ui.warn("The reviewer didn't return a verdict; accepting on the gates alone.")
        return evidence

    def analysis_call(self, prompt: str, run_dir: Path, schema_name: str, max_turns: int) -> AgentCall:
        """A read-only call (review, convergence): same hermetic setup, no write tools, no contract."""
        call = self.agent_call(prompt, run_dir, schema_name)
        call.tools, call.skip_permissions, call.system_append = list(REVIEW_TOOLS), False, ""
        call.max_turns, call.max_budget_usd, call.agents_file = max_turns, min(call.max_budget_usd, 5.0), None
        return call

    # 7. Commit (and update the unit)

    def apply(self, unit: Unit, units: dict[str, Unit], report: dict | None, judgement: Judgement,
              evidence: Evidence, changes: RunChanges) -> str:
        unit.wip_paths = sorted(set(unit.wip_paths) | set(changes.paths))
        unit.wip_overlap = sorted(set(unit.wip_overlap) | set(changes.overlapping))
        unit.acknowledged_impacts = sorted(set(unit.acknowledged_impacts) |
                                           set((report or {}).get("acknowledged_impacts", [])))
        if judgement.counts_attempt:
            unit.attempts += 1
        notes = _notes_for(judgement, evidence, report)
        if notes:
            self.store.append_note(unit.id, f"Attempt {unit.attempts}: {judgement.verdict.value}", notes)

        if judgement.verdict is Verdict.DONE:
            return self._commit_done(unit, units, report or {})
        if judgement.verdict is Verdict.BLOCKED:
            return self._block(unit, judgement.reason)
        if judgement.verdict is Verdict.RETRY:
            unit.status = Status.IN_PROGRESS
            unit.last_review_gaps = list(evidence.review.gaps) if evidence.review else []
        self.store.save(unit)
        return ""

    def _commit_done(self, unit: Unit, units: dict[str, Unit], report: dict) -> str:
        unit.status = Status.DONE
        unit.handoff = report.get("handoff", "").strip()
        unit.last_review_gaps = []
        work = unit.wip_paths
        unit.wip_paths, unit.wip_overlap = [], []
        added = self._add_discovered(unit, units, report.get("discovered_issues", []))
        self._capture_learnings(unit, report.get("learnings", []))
        unit.done = {"by": self.developer, "at": now_iso()}
        self.store.save(unit)
        state = [self.store.path(unit.id), self.store.notes_path(unit.id),
                 self.project.learnings_inbox] + [self.store.path(u) for u in added]
        state += self.write_ledger(unit, report, work)
        message = f"{unit.id}: {unit.title}\n\n{report.get('summary', '').strip()}\n\nGenerated-by: ralph (Claude Code)"
        sha = vcs.commit_paths(self.git, work + self._existing_relative(state), message)
        ui.ok(f"{unit.id} done and committed ({sha[:10]}).")
        if added:
            ui.info(f"  {len(added)} issue(s) found along the way are waiting for triage: {', '.join(added)}")
        return sha

    def _block(self, unit: Unit, reason: str) -> str:
        if unit.wip_paths:
            ref = vcs.save_rejected(self.git, unit.id, unit.attempts, unit.wip_paths)
            vcs.clean_paths(self.git, RunChanges(paths=unit.wip_paths, overlapping=unit.wip_overlap))
            self.store.append_note(unit.id, "Work saved", f"The unit's work is saved under `{ref}` "
                                   f"(inspect it with `git show {ref}`).")
        unit.status, unit.blocker = Status.BLOCKED, reason
        unit.wip_paths, unit.wip_overlap = [], []
        self.store.save(unit)
        state = self._existing_relative([self.store.path(unit.id), self.store.notes_path(unit.id)])
        sha = vcs.commit_paths(self.git, state, f"ralph: {unit.id} blocked\n\n{reason}")
        ui.warn(f"{unit.id} is blocked: {reason}")
        self.notifier.send("blocked", f"{unit.id} {unit.title} is blocked: {reason}", cr=self.cr.name, unit=unit.id)
        return sha

    def write_ledger(self, unit: Unit, report: dict, paths: list[str]) -> list[Path]:
        """The ledger entry for work about to be committed: what changed, and which
        interfaces (reported by the agent, cross-checked from the code)."""
        if not self.config.team.ledger:
            return []
        files = vcs.changed_entries(self.git, vcs.committable(self.git, paths))
        detected = interfaces.detect(self.git, [path for _, path in files])
        entry = ledger.new_entry(unit.id, self.cr.name, unit.title, self.developer, self.email, self.git.head(),
                                 files, interfaces.merge(report.get("interfaces_changed", []), detected),
                                 report.get("summary", ""), unit.acknowledged_impacts)
        return [ledger.write_entry(self.root, entry)]

    def _add_discovered(self, unit: Unit, units: dict[str, Unit], issues: list[str]) -> list[str]:
        added = []
        for issue in issues:
            new_id = discovered_unit_id(unit.id, issue)
            if new_id in units or self.store.path(new_id).exists():
                continue
            self.store.save(Unit(id=new_id, title=issue.strip()[:80], cr=self.cr.name, description=issue.strip(),
                                 discovered_from=unit.id, triage=True, priority="P3", order=unit.order))
            added.append(new_id)
        return added

    def _capture_learnings(self, unit: Unit, learnings: list[dict]) -> None:
        if not learnings:
            return
        inbox = self.project.learnings_inbox
        inbox.parent.mkdir(parents=True, exist_ok=True)
        with open(inbox, "a", encoding="utf-8") as handle:
            for item in learnings[:5]:
                handle.write(json.dumps({"unit": unit.id, "cr": self.cr.name, "at": now_iso(), **item},
                                        ensure_ascii=False) + "\n")

    def _existing_relative(self, paths: list[Path]) -> list[str]:
        return [str(p.relative_to(self.root)) for p in paths if p.exists()]

    # 8. Record

    def record(self, iteration: int, unit: Unit, outcome: str, result: AgentResult, snapshot: Snapshot,
               run_dir: Path, evidence: Evidence | None = None, commit: str = "", changed: int = 0,
               notes: str = "") -> None:
        summary = result.stats.summary()
        record = IterationRecord(
            run_id=self.run_id, iteration=iteration, cr=self.cr.name, unit=unit.id, attempt=unit.attempts,
            outcome=outcome, developer=self.developer, model=self.options.model or self.config.runner.model_build,
            started_at=now_iso(), head_before=snapshot.head, head_after=self.git.head(), agent=summary,
            gates=[{"name": g.name, "passed": g.passed, "exit_code": g.exit_code, "seconds": round(g.seconds, 1)}
                   for g in (evidence.gates if evidence else [])],
            review=evidence.review.verdict if evidence and evidence.review else "",
            review_cost_usd=evidence.review.cost_usd if evidence and evidence.review else 0.0,
            integrity_failures=evidence.integrity.failures if evidence and evidence.integrity else [],
            changed_files=changed, commit=commit, run_dir=str(run_dir.relative_to(self.root)), notes=notes)
        append_record(self.project.iterations_log, record)
        minutes = (summary.get("duration_ms") or 0) / 60000
        ui.info(f"  {unit.id} · {outcome} · {summary.get('num_turns') or 0} turns · {minutes:.1f} min · "
                f"${summary.get('total_cost_usd') or 0:.2f} · peak context {summary.get('peak_context', 0):,} "
                f"· {summary.get('compactions', 0)} compactions · {summary.get('subagent_calls', 0)} subagent calls")
        if notes and outcome not in ("done",):
            ui.info(f"  Why: {notes}")

    # 9. Decide

    def decide(self, judgement: Judgement, got_result: bool, changes: RunChanges) -> bool:
        reason = ""
        self.invalid_results = 0 if got_result else self.invalid_results + 1
        made_progress = judgement.verdict is Verdict.DONE or bool(changes.paths)
        self.stalls = 0 if made_progress else self.stalls + 1
        if judgement.verdict is Verdict.INFRA:
            reason = judgement.reason
        elif self.invalid_results >= self.config.loop.max_invalid_results:
            reason = f"{self.invalid_results} iterations in a row without a valid result from the agent"
        elif self.config.loop.max_stalls and self.stalls >= self.config.loop.max_stalls:
            reason = f"{self.stalls} iterations in a row changed nothing"
        if reason:
            ui.error("Stopping: " + reason)
            self.notifier.send("stopped", reason, cr=self.cr.name)
        return bool(reason)

    # ── When nothing is ready ────────────────────────────────────────────────

    def finish(self, units: dict[str, Unit]) -> bool:
        """Report why the loop is ending. Returns True if the loop should continue
        (the convergence check found gaps and added units for them)."""
        if any(u.status is not Status.DONE and not u.triage for u in units.values()):
            ui.warn("Nothing is ready to run:")
            reasons = why_nothing_is_ready(units)
            for reason in reasons:
                ui.info(f"    {reason}")
            self.notifier.send("stopped", "nothing is ready to run: " + "; ".join(reasons[:3]), cr=self.cr.name)
            return False
        triage = [u.id for u in units.values() if u.triage]
        ui.ok(f"Every planned unit of {self.cr.name} is done."
              + (f" ({len(triage)} found issue(s) wait for triage.)" if triage else ""))
        if self.config.verify.done:
            result = gate_runner.run_gate("done", self.config.verify.done, self.root, self.run_dir / "done.log",
                                          self.config.verify.gate_timeout_minutes * 60)
            if not result.passed:
                ui.warn(f"The done check failed (exit {result.exit_code}); {self.cr.name} isn't complete yet. "
                        f"Log: {result.log_path}")
                return False
        if self.config.loop.converge and not self.converged_once:
            self.converged_once = True
            if self._converge(units):
                return True
        self._tag()
        return False

    def _converge(self, units: dict[str, Unit]) -> bool:
        spec = parse_spec(self.cr.spec)
        criteria = _acceptance_criteria(spec)
        if not criteria:
            ui.info("  No acceptance criteria found in the spec; skipping the convergence check.")
            return False
        ui.info("  Convergence check: comparing the implementation with the spec's acceptance criteria…")
        prompt = render("converge.md", CRITERIA=criteria, SPEC_PATH=str(self.cr.spec))
        result = self.runner.run(self.analysis_call(prompt, self.run_dir / "converge",
                                                    "converge-result.json", max_turns=80))
        data = result.structured
        if not data:
            ui.warn("The convergence check didn't return a result; not tagging. Run it again with 'ralph start'.")
            self.converged_once = False
            return False
        if data.get("converged") or not data.get("gaps"):
            ui.ok("Converged: every acceptance criterion is met.")
            return False
        added = []
        for gap in data["gaps"]:
            title = f"{gap['criterion']}: {gap['detail']}"[:80]
            unit_id = discovered_unit_id("U000", title)
            if unit_id in units:
                continue
            self.store.save(Unit(id=unit_id, title=title, cr=self.cr.name, description=gap["detail"],
                                 discovered_from="convergence", priority="P1", order=10**5))
            added.append(unit_id)
        if added:
            vcs.commit_paths(self.git, self._existing_relative([self.store.path(u) for u in added]),
                             f"ralph: {len(added)} unit(s) from the convergence check on {self.cr.name}")
            ui.warn(f"Not converged: {len(added)} gap(s) became new units ({', '.join(added)}). Continuing.")
        return bool(added)

    def _tag(self) -> None:
        tag_format = self.config.loop.tag_format
        if not tag_format:
            return
        tag = tag_format.format(cr=self.cr.name, n=self.cr.number)
        if self.git.run("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False).returncode == 0:
            return
        self.git.run("tag", "-a", tag, "-m", f"ralph: {self.cr.name} complete")
        ui.ok(f"Tagged {tag} (local; push it when you're ready).")
        self.notifier.send("complete", f"{self.cr.name} is complete and tagged {tag}", cr=self.cr.name, tag=tag)


def result_log(run_dir: Path) -> Path:
    return run_dir / "stream.jsonl"


def _lesson_line(lesson) -> str:
    context = [f"when {lesson.trigger.strip()}"] if lesson.trigger.strip() else []
    context += [f"applies to {', '.join(lesson.paths)}"] if lesson.paths else []
    return lesson.rule.strip() + (f" ({'; '.join(context)})" if context else "") + f" — from {lesson.unit}"


def _notes_for(judgement: Judgement, evidence: Evidence, report: dict | None) -> str:
    """What the next attempt needs to know: why this one didn't finish."""
    if judgement.verdict is Verdict.DONE:
        return (report or {}).get("summary", "")
    parts = [judgement.reason]
    failure = gate_runner.failure_summary(evidence.gates)
    if failure:
        parts.append(failure)
    if evidence.review and evidence.review.verdict == "changes_requested":
        parts.append("Review gaps:\n" + evidence.review.as_notes())
    if evidence.integrity and evidence.integrity.failures:
        parts.append("Integrity:\n" + "\n".join(f"- {f}" for f in evidence.integrity.failures))
    if report and report.get("summary"):
        parts.append(f"The agent said: {report['summary']}")
    return "\n\n".join(parts)


def _acceptance_criteria(spec: Spec | None) -> str:
    """The spec's acceptance criteria: its "acceptance criteria" section, else lines with AC- IDs."""
    if spec is None:
        return ""
    for section in spec.sections.values():
        if "acceptance criteria" in section.title.lower():
            return section.text
    return spec.lines_mentioning("AC", context=0) if any("AC-" in line for line in spec.lines) else ""
