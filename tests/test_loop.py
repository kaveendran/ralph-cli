"""The loop kernel end to end, with a scripted agent instead of Claude.

Each test builds a small git project with units, a plan and gates, scripts
what the "agent" does on each run, and checks what the harness did: unit
state, commits, saved work and the iteration log.
"""

import contextlib
import io
import json
import os
import unittest
from unittest import mock

from ralph.config import Config
from ralph.errors import RalphError
from ralph.kernel.loop import Kernel, StartOptions
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.paths import ProjectPaths
from tests import helpers
from tests.helpers import (ScriptedRunner, TempDirTestCase, capped_step, done_step, failed_step, git,
                           make_repo, report_step, usage_limit_step)

PLAN = """\
## Unit: First (ID: U001)
### Acceptance Criteria
- [ ] WHEN called, THE SYSTEM SHALL work.

## Unit: Second (ID: U002)
### Dependencies
- **Depends On:** [U001]
"""


class LoopTestCase(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "project")
        self._path = os.environ["PATH"]
        helpers.fake_claude_on_path(self.tmp / "bin")
        self.project = ProjectPaths(self.root)
        cr = self.project.cr("cr1")
        (cr.dir / "plan").mkdir(parents=True)
        (cr.dir / "plan" / "cr1-implementation-plan.md").write_text(PLAN)
        self.store = UnitStore(cr.units_dir)
        self.store.save(Unit("U001", "First", "cr1", order=1))
        self.store.save(Unit("U002", "Second", "cr1", order=2, depends_on=["U001"]))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "plan and units")
        (self.root / "notes-to-self.txt").write_text("uncommitted, not the agent's\n")
        self.config = Config()
        self.config.verify.gates = {"unit": "test ! -f FAIL"}     # fails while a FAIL file exists
        self.config.verify.review = "off"
        self.config.loop.retry_delay_seconds = 0
        self.config.loop.converge = False

    def tearDown(self):
        os.environ["PATH"] = self._path
        super().tearDown()

    def kernel(self, *steps, **options) -> tuple[Kernel, ScriptedRunner]:
        runner = ScriptedRunner(*steps)
        defaults = dict(cr="cr1", skip_baseline=True)
        defaults.update(options)
        return Kernel(self.project, self.config, runner, StartOptions(**defaults)), runner

    def units(self) -> dict[str, Unit]:
        return self.store.load()

    def committed_files(self, ref: str = "HEAD") -> set[str]:
        return set(git(self.root, "show", "--name-only", "--format=", ref).split())

    def records(self) -> list[dict]:
        return [json.loads(line) for line in self.project.iterations_log.read_text().splitlines()]


class HappyPath(LoopTestCase):
    def test_units_run_in_dependency_order_and_commit_only_their_own_files(self):
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a = 1\n"}, handoff="a.py has a"),
                                     done_step("U002", {"b.py": "b = 2\n"}))
        kernel.run()
        units = self.units()
        self.assertEqual((units["U001"].status, units["U002"].status), (Status.DONE, Status.DONE))
        self.assertEqual(units["U001"].handoff, "a.py has a")
        self.assertIn("b.py", self.committed_files())
        self.assertNotIn("notes-to-self.txt", self.committed_files())
        self.assertIn("notes-to-self.txt", git(self.root, "status", "--porcelain"))
        self.assertIn("a.py has a", runner.calls[1].prompt)          # U002's pack carries U001's handoff
        self.assertEqual([r["outcome"] for r in self.records()], ["done", "done"])

    def test_the_agent_gets_the_contract_schema_and_hermetic_setup(self):
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a\n"}), max_iterations=1)
        kernel.run()
        call = runner.calls[0]
        self.assertIn("Ralph loop contract", call.system_append)
        self.assertEqual(call.json_schema["required"], ["unit", "status", "summary"])
        self.assertTrue(call.hermetic)
        self.assertEqual(call.env["CLAUDE_CODE_AUTO_COMPACT_WINDOW"], "200000")
        self.assertTrue(call.settings_file.exists())

    def test_an_agent_commit_is_undone_and_redone_by_the_harness(self):
        kernel, _ = self.kernel(done_step("U001", {"a.py": "a\n"}, commit=True), max_iterations=1)
        kernel.run()
        self.assertNotIn("agent commit", git(self.root, "log", "--format=%s"))
        self.assertIn("U001: First", git(self.root, "log", "-1", "--format=%s"))

    def test_discovered_issues_wait_for_triage(self):
        kernel, _ = self.kernel(done_step("U001", {"a.py": "a\n"}, issues=["the logger leaks"]), max_iterations=1)
        kernel.run()
        triage = [u for u in self.units().values() if u.triage]
        self.assertEqual(len(triage), 1)
        self.assertEqual(triage[0].discovered_from, "U001")


class Retries(LoopTestCase):
    def test_a_failing_gate_retries_and_the_final_commit_includes_every_attempt(self):
        kernel, runner = self.kernel(
            done_step("U001", {"part1.py": "x\n", "FAIL": "boom\n"}),   # gate fails
            done_step("U001", {"part2.py": "y\n"}),                     # still failing
            lambda call: (os.remove(call.cwd / "FAIL"), done_step("U001", {})(call))[1],
            max_iterations=3)
        kernel.run()
        unit = self.units()["U001"]
        self.assertIs(unit.status, Status.DONE)
        self.assertIn("Gate `unit` failed", runner.calls[1].prompt)      # the next attempt sees why
        self.assertTrue({"part1.py", "part2.py"} <= self.committed_files())

    def test_gives_up_after_three_attempts_and_saves_the_work(self):
        kernel, _ = self.kernel(*[report_step("U001", "partial", {"half.py": f"v{n}\n"}) for n in range(3)],
                                max_iterations=3)
        kernel.run()
        unit = self.units()["U001"]
        self.assertIs(unit.status, Status.BLOCKED)
        self.assertIn("gave up after 3 attempts", unit.blocker)
        self.assertFalse((self.root / "half.py").exists())                # tree cleaned…
        self.assertIn("half.py", self.committed_files("refs/ralph/rejected/U001/3"))   # …work saved
        self.assertEqual((self.root / "notes-to-self.txt").read_text(), "uncommitted, not the agent's\n")

    def test_skip_markers_fail_the_integrity_check(self):
        kernel, runner = self.kernel(done_step("U001", {"tests/test_a.py": "@pytest.mark.skip\ndef test(): pass\n"}),
                                     max_iterations=1)
        kernel.run()
        self.assertIs(self.units()["U001"].status, Status.IN_PROGRESS)
        self.assertIn("skip", self.records()[0]["integrity_failures"][0])

    def test_agent_reported_block(self):
        kernel, _ = self.kernel(report_step("U001", "blocked", blocker="spec §3 contradicts §4"), max_iterations=1)
        kernel.run()
        self.assertEqual(self.units()["U001"].blocker, "spec §3 contradicts §4")


class StopRules(LoopTestCase):
    def test_infrastructure_trouble_stops_without_counting_an_attempt(self):
        self.config.verify.gates = {"unit": "echo 'Cannot connect to the Docker daemon'; exit 1"}
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a\n"}), done_step("U001", {}))
        kernel.run()
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(self.units()["U001"].attempts, 0)

    def test_three_failed_runs_stop_the_loop_without_attempts(self):
        kernel, runner = self.kernel(failed_step, failed_step, failed_step, failed_step)
        kernel.run()
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(self.units()["U001"].attempts, 0)

    def test_usage_limit_stops(self):
        kernel, runner = self.kernel(usage_limit_step, done_step("U001", {}))
        kernel.run()
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(self.records()[0]["outcome"], "usage_limit")

    def test_a_capped_run_keeps_its_work_for_the_next_attempt(self):
        def capped_with_edit(call):
            (call.cwd / "started.py").write_text("partial\n")
            return capped_step(call)
        kernel, runner = self.kernel(capped_with_edit, done_step("U001", {"rest.py": "r\n"}), max_iterations=2)
        kernel.run()
        self.assertIs(self.units()["U001"].status, Status.DONE)
        self.assertTrue({"started.py", "rest.py"} <= self.committed_files())

    def test_iterations_that_change_nothing_stop_the_loop(self):
        self.config.loop.max_stalls = 2
        kernel, runner = self.kernel(report_step("U001", "partial"), report_step("U001", "partial"),
                                     report_step("U001", "partial"))
        kernel.run()
        self.assertEqual(len(runner.calls), 2)


class Preflight(LoopTestCase):
    def test_refuses_the_default_branch(self):
        git(self.root, "branch", "-m", "main")
        kernel, _ = self.kernel()
        with self.assertRaises(RalphError) as raised:
            kernel.run()
        self.assertIn("main", raised.exception.message)

    def test_a_red_baseline_stops_before_any_unit(self):
        (self.root / "FAIL").write_text("already broken\n")
        kernel, runner = self.kernel(skip_baseline=False)
        with self.assertRaises(RalphError):
            kernel.run()
        self.assertEqual(runner.calls, [])

    def test_a_project_with_no_tests_yet_passes_the_baseline(self):
        self.config.verify.gates = {"unit": "exit 5"}          # what pytest and unittest do with no tests
        kernel, runner = self.kernel(report_step("U001", "partial", {"a.py": "a\n"}), skip_baseline=False,
                                     max_iterations=1)
        kernel.run()
        self.assertEqual(len(runner.calls), 1)

    def test_a_pinned_version_must_match(self):
        self.config.runner.claude_version = "9.9.9"
        kernel, _ = self.kernel()
        with self.assertRaises(RalphError) as raised:
            kernel.run()
        self.assertIn("pins 9.9.9", raised.exception.message)

    def test_steering_reaches_the_next_pack_once(self):
        from ralph.kernel.control import Control
        Control(self.project.control_dir).add_steer("use the existing helper in utils.py")
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a\n"}), done_step("U002", {"b.py": "b\n"}))
        kernel.run()
        self.assertIn("use the existing helper", runner.calls[0].prompt)
        self.assertNotIn("use the existing helper", runner.calls[1].prompt)


class MemoryAndSkills(LoopTestCase):
    def write_skill(self, name: str, frontmatter: str) -> None:
        folder = self.root / ".claude" / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\n{frontmatter}---\n# {name}\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", f"skill {name}")

    def test_lessons_are_committed_with_the_unit_and_reach_later_packs(self):
        lesson = {"trigger": "gates failed on formatting", "rule": "Run make fmt before finishing", "paths": []}
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a\n"}, learnings=[lesson]),
                                     done_step("U002", {"b.py": "b\n"}))
        kernel.run()
        (saved,) = [json.loads(line) for line in self.project.learnings_inbox.read_text().splitlines()]
        self.assertEqual((saved["unit"], saved["rule"]), ("U001", "Run make fmt before finishing"))
        self.assertIn(".ralph/learnings/inbox.jsonl", self.committed_files("HEAD~1"))
        self.assertNotIn("Run make fmt", runner.calls[0].prompt)
        self.assertIn("- Run make fmt before finishing (when gates failed on formatting) — from U001",
                      runner.calls[1].prompt)

    def test_a_skill_for_the_units_files_is_suggested_in_its_pack(self):
        self.store.save(Unit("U001", "First", "cr1", order=1, key_files=["backend/api.py"]))
        self.write_skill("api-conventions", "description: Writes endpoints.\npaths: [backend/**]\n")
        kernel, runner = self.kernel(done_step("U001", {"backend/api.py": "a\n"}), max_iterations=1)
        kernel.run()
        self.assertIn("- `api-conventions`: its paths match this unit's files", runner.calls[0].prompt)

    def test_a_blocked_unit_and_the_stop_that_follows_are_notified(self):
        events = self.tmp / "events.jsonl"
        self.config.notify.command = f"cat >> '{events}'; echo >> '{events}'"
        kernel, _ = self.kernel(report_step("U001", "blocked", blocker="needs an API key"))
        kernel.run()
        sent = [json.loads(line) for line in events.read_text().splitlines() if line.strip()]
        self.assertEqual([e["event"] for e in sent], ["blocked", "stopped"])
        self.assertIn("needs an API key", sent[0]["text"])
        self.assertIn("U002", sent[1]["text"])          # why nothing else can run

    def test_preflight_warns_about_a_stale_skill_but_runs(self):
        (self.root / "requirements.txt").write_text("fastapi==0.116.1\n")
        self.write_skill("api", 'description: Writes endpoints.\nralph-versions:\n  fastapi: "0.115"\n')
        kernel, _ = self.kernel(done_step("U001", {"a.py": "a\n"}), max_iterations=1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertIn("Skill 'api' was written for fastapi 0.115, but requirements.txt has 0.116.1", out.getvalue())
        self.assertEqual(self.units()["U001"].status, Status.DONE)



def deletes_step(path: str, unit: str = "task"):
    """A step that deletes a file and reports done (e.g. a repair)."""
    def step(call):
        (call.cwd / path).unlink()
        return helpers.agent_result(call, helpers.result_events({"unit": unit, "status": "done", "summary": "fixed"}))
    return step


class Safeguards(LoopTestCase):
    def test_a_failing_health_check_makes_repair_the_next_task(self):
        (self.root / "BROKEN").write_text("x\n")
        git(self.root, "add", "BROKEN")
        git(self.root, "commit", "-qm", "break the tree")
        self.config.verify.health = "test ! -f BROKEN"
        kernel, runner = self.kernel(deletes_step("BROKEN"), done_step("U001", {"a.py": "a\n"}), max_iterations=2)
        kernel.run()
        self.assertIn("Repair the failing health check", runner.calls[0].prompt)
        self.assertIn("Command: `test ! -f BROKEN` (exit 1)", runner.calls[0].prompt)
        self.assertIn("`test ! -f BROKEN` (health)", runner.calls[0].prompt)        # a check the repair must pass
        self.assertEqual(git(self.root, "log", "-2", "--format=%s").splitlines(),
                         ["U001: First", "task: Repair the failing health check"])
        self.assertIs(self.units()["U001"].status, Status.DONE)

    def test_the_loop_stops_when_repairs_keep_failing(self):
        self.config.verify.health = "false"
        kernel, runner = self.kernel(report_step("task", "partial"), report_step("task", "partial"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertEqual(len(runner.calls), 2)
        self.assertIn("the health check still fails after 2 repair attempts", out.getvalue())

    def test_the_constitution_reaches_the_builder_and_the_reviewer(self):
        self.project.constitution.parent.mkdir(exist_ok=True)
        self.project.constitution.write_text("# Project principles\n1. All money is integer cents.\n")
        self.config.verify.review = "all"
        review = lambda call: helpers.agent_result(call, helpers.result_events(
            {"verdict": "changes_requested", "gaps": [], "rule_violations": ["price is a float"]}))
        kernel, runner = self.kernel(done_step("U001", {"a.py": "price = 1.5\n"}), review, max_iterations=1)
        kernel.run()
        self.assertIn("1. All money is integer cents.", runner.calls[0].prompt)      # the pack
        self.assertIn("1. All money is integer cents.", runner.calls[1].prompt)      # the review
        self.assertIsNot(self.units()["U001"].status, Status.DONE)
        self.assertIn("price is a float", self.store.notes_path("U001").read_text())

    def test_a_run_stops_at_its_budget(self):
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a\n"}), done_step("U002", {"b.py": "b\n"}),
                                     run_budget_usd=0.4)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertEqual(len(runner.calls), 1)                     # each scripted run costs $0.50
        self.assertIn("this run has spent $0.50, over its $0.4 budget", out.getvalue())

    def test_units_that_stop_being_done_stop_the_loop(self):
        def undoes_the_first(call):
            unit = self.store.load()["U001"]
            unit.status = Status.TODO                              # e.g. a bad merge while paused
            self.store.save(unit)
            return done_step("U002", {"b.py": "b\n"})(call)
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a\n"}), undoes_the_first, done_step("U001", {}))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertIn("Stopping: U001 was done and isn't any more", out.getvalue())
        self.assertEqual(len(runner.calls), 2)

    def test_a_unit_that_keeps_compacting_is_marked_too_big(self):
        def compacting(call):
            events = helpers.result_events({"unit": "U001", "status": "done", "summary": "s"})
            events.insert(1, {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"pre_tokens": 1}})
            events.insert(1, {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"pre_tokens": 1}})
            (call.cwd / "a.py").write_text("a\n")
            return helpers.agent_result(call, events)
        kernel, _ = self.kernel(compacting, max_iterations=1)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertEqual(self.units()["U001"].size_warning, "compacted 2 times")
        self.assertIn("Too big for one session?", self.store.notes_path("U001").read_text())



class UsageLimitAndSubagents(LoopTestCase):
    def test_waiting_through_a_usage_limit_doesnt_count_toward_max(self):
        self.config.loop.on_usage_limit = "wait"
        clock = [1_000_000.0]
        def limited(call):
            return helpers.agent_result(call, [{"type": "rate_limit_event", "rate_limit_info":
                                                {"status": "rejected", "resetsAt": int(clock[0]) + 600}},
                                               *helpers.result_events(None)], exit_code=1)
        kernel, runner = self.kernel(limited, done_step("U001", {"a.py": "a\n"}), max_iterations=1)
        out = io.StringIO()
        with mock.patch("ralph.kernel.loop.time.time", side_effect=lambda: clock[0]), \
                mock.patch("ralph.kernel.loop.time.sleep", side_effect=lambda s: clock.__setitem__(0, clock[0] + s)), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertIn("waiting until", out.getvalue())
        self.assertEqual(len(runner.calls), 2)                     # the wait didn't use up --max 1
        self.assertIs(self.units()["U001"].status, Status.DONE)

    def test_a_far_off_reset_still_stops_the_loop(self):
        self.config.loop.on_usage_limit = "wait"
        self.config.loop.max_wait_hours = 1
        far = lambda call: helpers.agent_result(call, [{"type": "rate_limit_event", "rate_limit_info":
                                                        {"status": "rejected", "resetsAt": 9_999_999_999}},
                                                       *helpers.result_events(None)], exit_code=1)
        kernel, runner = self.kernel(far)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertIn("the reset is too far off", out.getvalue())

    def test_preflight_makes_the_lessons_inbox_merge_line_by_line(self):
        kernel, _ = self.kernel()
        with contextlib.redirect_stdout(io.StringIO()):
            kernel.preflight(need_units=True)
        path = self.root / ".git" / "info" / "attributes"
        self.assertIn(".ralph/learnings/*.jsonl merge=union", path.read_text())
        with contextlib.redirect_stdout(io.StringIO()):
            kernel.preflight(need_units=True)                       # again: nothing is added twice
        self.assertEqual(path.read_text().count("merge=union"), 1)

    def test_runs_get_the_explorer_and_the_test_runner(self):
        kernel, runner = self.kernel(done_step("U001", {"a.py": "a\n"}), max_iterations=1)
        kernel.run()
        agents = json.loads(runner.calls[0].agents_file.read_text())
        self.assertEqual(sorted(agents), ["explorer", "test-runner"])
        self.assertIn("Bash", agents["test-runner"]["tools"])
        self.assertIn("test-runner", runner.calls[0].system_append)


if __name__ == "__main__":
    unittest.main()
