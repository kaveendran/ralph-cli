"""The loop kernel end to end, with a scripted agent instead of Claude.

Each test builds a small git project with units, a plan and gates, scripts
what the "agent" does on each run, and checks what the harness did: unit
state, commits, saved work and the iteration log.
"""

import json
import os
import unittest

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


if __name__ == "__main__":
    unittest.main()
