"""The CLI commands, run in a temporary project."""

import contextlib
import io
import os
import unittest

from ralph.cli import main
from ralph.config import Config
from ralph.kernel.loop import Kernel, StartOptions
from ralph.kernel.modes import run_task
from ralph.model.store import UnitStore
from ralph.model.unit import Status
from ralph.paths import ProjectPaths
from tests import helpers
from tests.helpers import SAMPLE_PLAN, ScriptedRunner, TempDirTestCase, done_step, git, make_repo


class Commands(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "project")
        self._cwd, self._path = os.getcwd(), os.environ["PATH"]
        helpers.fake_claude_on_path(self.tmp / "bin")
        os.chdir(self.root)
        self.project = ProjectPaths(self.root)
        plan = self.project.cr("cr2").dir / "plan" / "cr2-implementation-plan.md"
        plan.parent.mkdir(parents=True)
        plan.write_text(SAMPLE_PLAN)

    def tearDown(self):
        os.chdir(self._cwd)
        os.environ["PATH"] = self._path
        super().tearDown()

    def ralph(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = main(list(args))
        return code, out.getvalue()

    def test_compile_rejects_oversized_units_unless_allowed(self):
        plan = self.project.cr("cr2").plan
        plan.write_text(plan.read_text().replace(
            "### Deliverables (files)", "### Deliverables (files)\n" + "".join(f"- `f{n}.py` — x\n" for n in range(9))))
        code, output = self.ralph("graph", "compile", "--cr", "2")
        self.assertEqual(code, 1)
        self.assertIn("too big", output)
        self.assertFalse(UnitStore(self.project.cr("cr2").units_dir).exists())
        code, _ = self.ralph("graph", "compile", "--cr", "2", "--allow-large")
        self.assertEqual(code, 0)
        self.assertTrue(UnitStore(self.project.cr("cr2").units_dir).load()["U001"].allow_large)
        self.assertIn("ralph: compile the cr2 plan", git(self.root, "log", "-1", "--format=%s"))

    def test_graph_ready_status_and_set(self):
        self.ralph("graph", "compile", "--cr", "2", "--allow-large")
        code, output = self.ralph("graph", "--cr", "2", "--mermaid")
        self.assertIn("U001 --> U002", output)
        code, output = self.ralph("ready", "--cr", "2")
        self.assertIn("U001", output)
        self.assertNotIn("U003", output)
        code, output = self.ralph("set", "U001", "--cr", "2", "--status", "done")
        code, output = self.ralph("status", "--cr", "2", "--write")
        self.assertIn("1/3 units", output)
        self.assertTrue(self.project.cr("cr2").progress_view.exists())

    def test_migrate_dry_run_writes_nothing(self):
        cr = self.project.cr("cr2")
        cr.legacy_progress.parent.mkdir(parents=True)
        cr.legacy_progress.write_text("- [x] U001 Search\n")
        code, output = self.ralph("migrate", "--cr", "2", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("3 units", output.replace("Units: 3", "3 units"))
        self.assertFalse(cr.units_dir.exists())
        self.assertFalse(self.project.config.exists())
        code, _ = self.ralph("migrate", "--cr", "2")
        self.assertIs(UnitStore(cr.units_dir).load()["U001"].status, Status.DONE)

    def test_errors_are_messages_not_tracebacks(self):
        code, output = self.ralph("start", "--cr", "9")
        self.assertEqual(code, 1)
        self.assertIn("There is no", output)
        self.assertNotIn("Traceback", output)

    def test_legacy_routing(self):
        code, output = self.ralph("legacy", "no-such-command")
        self.assertEqual(code, 1)
        self.assertIn("no original command", output)

    def test_task_mode_commits_only_after_the_gates(self):
        config = Config()
        config.verify.gates = {"unit": "test -f fixed.txt"}
        runner = ScriptedRunner(done_step("task", {"fixed.txt": "ok\n"}))
        kernel = Kernel(self.project, config, runner, StartOptions(cr="cr2", skip_baseline=True))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_task(kernel, "fix the flaky test", "The date test fails at midnight."), 0)
        self.assertIn("task: fix the flaky test", git(self.root, "log", "-1", "--format=%s"))
        self.assertIn("The date test fails at midnight.", runner.calls[0].prompt)


if __name__ == "__main__":
    unittest.main()
