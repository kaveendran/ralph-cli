"""Setting up projects and CRs: `ralph init`, `add-cr`, `status` before the build,
and the quiet test scripts. These replace the scenarios of the old bash
test/e2e.sh that still apply."""

import contextlib
import io
import os
import stat
import subprocess
import sys
import tomllib
from unittest import mock

from ralph.cli import main
from ralph.commands.scaffold import TEST_INFRA_DIR, TYPES
from ralph.config import Config
from ralph.kernel.loop import Kernel, StartOptions
from ralph.kernel.modes import run_plan
from ralph.model.store import UnitStore
from ralph.paths import ProjectPaths
from tests import helpers
from tests.helpers import (SAMPLE_PLAN, SAMPLE_SPEC, ScriptedRunner, TempDirTestCase, agent_result, make_repo,
                           result_events)


class Scaffold(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "project")
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.project = ProjectPaths(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        super().tearDown()

    def ralph(self, *args: str, answers: str = "\n\n\n") -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch.object(sys, "stdin", io.StringIO(answers)):
            try:
                code = main(list(args))
            except SystemExit as exit_:
                code = exit_.code
        return code, out.getvalue()

    def test_help_lists_every_command(self):
        code, output = self.ralph("--help")
        for command in ("init", "add-cr", "start", "status", "critique", "refine-plan", "retro", "deploy",
                        "sync", "memory", "skills", "migrate"):
            self.assertIn(command, output)
        self.assertNotIn("legacy", output)

    def test_init_creates_the_project_files_without_placeholders(self):
        code, output = self.ralph("init", "--name", "Shop", "--description", "Sells things", "--type", "feature")
        self.assertEqual(code, 0, output)
        initial = self.project.cr("initial")
        for path in (self.root / "CLAUDE.md", self.project.config, initial.spec, initial.plan_prompt, initial.instructions):
            self.assertTrue(path.exists(), path)
            self.assertNotIn("{{", path.read_text(), path)
        self.assertIn("log/", (self.root / ".gitignore").read_text())
        self.assertFalse((self.root / "scripts").exists())        # test scripts only with --test-infra
        code, output = self.ralph("init", "--name", "Shop", "--description", "x", "--type", "feature")
        self.assertIn("skip", output)                             # a second run keeps your files

    def test_init_with_test_infra_writes_quiet_scripts_and_their_gates(self):
        code, output = self.ralph("init", "--name", "Shop", "--description", "x", "--type", "backend+frontend",
                                  "--test-infra")
        self.assertEqual(code, 0, output)
        for name in ("docker-compose.test.yml", "scripts/test-unit.sh", "scripts/test-api.sh", "scripts/test-e2e.sh",
                     "e2e/playwright.config.ts", ".ralph/mcp.json"):
            self.assertTrue((self.root / name).exists(), name)
        self.assertTrue(os.access(self.root / "scripts" / "test-unit.sh", os.X_OK))
        gates = tomllib.loads(self.project.config.read_text())["verify"]["gates"]
        self.assertEqual(set(gates), {"unit", "api", "e2e"})
        backend_only = make_repo(self.tmp / "api-only")
        os.chdir(backend_only)
        self.ralph("init", "--name", "API", "--description", "x", "--type", "backend", "--test-infra")
        self.assertTrue((backend_only / "scripts" / "test-api.sh").exists())
        self.assertFalse((backend_only / "scripts" / "test-e2e.sh").exists())

    def test_status_says_what_to_do_before_the_build(self):
        self.ralph("init", "--name", "Shop", "--description", "x", "--type", "feature")
        code, output = self.ralph("status")
        self.assertEqual(code, 0, output)
        self.assertIn("Step 1 of 3 — write the spec: development/initial/spec/spec.md", output)
        self.project.cr("initial").spec.write_text("# Spec\n\n## 1. Overview\nA shop.\n")
        code, output = self.ralph("status")
        self.assertIn("Step 2 of 3 — plan it: ralph start --mode plan --cr initial", output)
        self.project.cr("initial").plan.write_text("## Unit: One (ID: U001)\n")
        code, output = self.ralph("status")
        self.assertIn("ralph graph compile --cr initial", output)

    def test_add_cr_for_every_type_with_all_flags(self):
        self.ralph("init", "--name", "Shop", "--description", "x", "--type", "feature")
        for number, impl_type in enumerate(TYPES, start=1):
            code, output = self.ralph("add-cr", "--number", str(number), "--title", f"Change {number}",
                                      "--type", impl_type, "--notes", "Keep the API stable.")
            self.assertEqual(code, 0, output)
            cr = self.project.cr(f"cr{number}")
            self.assertIn(f"Change {number}", cr.spec.read_text())
            self.assertIn("Keep the API stable.", cr.instructions.read_text())
            self.assertIn(impl_type, cr.plan_prompt.read_text())
            for path in (cr.spec, cr.plan_prompt, cr.instructions):
                self.assertNotIn("{{", path.read_text(), path)
        self.assertIn("FastAPI", self.project.cr("cr2").instructions.read_text())    # backend constraints

    def test_add_cr_with_a_prior_cr_and_a_spec_file(self):
        self.ralph("init", "--name", "Shop", "--description", "x", "--type", "feature")
        self.ralph("add-cr", "--number", "1", "--title", "Search")
        spec_file = self.tmp / "my-spec.md"
        spec_file.write_text("# My own spec\n\n## 1. Overview\nFilters.\n")
        code, output = self.ralph("add-cr", "--number", "2", "--title", "Filters", "--prior-cr", "1")
        self.assertEqual(code, 0, output)
        cr2 = self.project.cr("cr2")
        self.assertIn("It builds on cr1: see `development/cr1/spec/spec.md`.", cr2.spec.read_text())
        self.assertIn("## Baseline\n\nThis CR builds on cr1.", cr2.instructions.read_text())
        self.ralph("add-cr", "--number", "3", "--spec-file", str(spec_file))
        self.assertEqual(self.project.cr("cr3").spec.read_text(), spec_file.read_text())
        self.assertNotIn("Baseline", self.project.cr("cr3").instructions.read_text())

    def test_add_cr_refuses_bad_input_with_a_message(self):
        for args, message in ((("--number", "9", "--prior-cr", "7"), "there's no development/cr7"),
                              (("--number", "9", "--spec-file", "nope.md"), "doesn't exist"),
                              (("--number", "initial"), "Use a number")):
            code, output = self.ralph("add-cr", *args)
            self.assertEqual(code, 1, args)
            self.assertIn(message, output)
            self.assertNotIn("Traceback", output)


class QuietTestScripts(TempDirTestCase):
    """scripts/test-unit.sh: ✓ when it passes, the failing tail when it doesn't."""

    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "project")
        script = self.root / "scripts" / "test-unit.sh"
        script.parent.mkdir()
        script.write_text((TEST_INFRA_DIR / "test-unit.sh").read_text())
        (self.root / "backend").mkdir()
        self.bin = self.tmp / "bin"
        self.bin.mkdir()

    def run_with_pytest_exit(self, code: int) -> subprocess.CompletedProcess:
        fake = self.bin / "python"              # stands in for `python -m pytest`
        fake.write_text(f"#!/bin/sh\necho 'collected 3 items'\necho 'FAILED test_x.py::test_a - KeyError'\nexit {code}\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}"}
        return subprocess.run(["bash", "scripts/test-unit.sh"], cwd=self.root, env=env, capture_output=True, text=True)

    def test_pass_fail_and_no_tests(self):
        passed = self.run_with_pytest_exit(0)
        self.assertEqual((passed.returncode, passed.stdout), (0, "✓ backend tests\n"))
        failed = self.run_with_pytest_exit(1)
        self.assertEqual(failed.returncode, 1)
        self.assertIn("✗ backend tests (exit 1)\ncollected 3 items\nFAILED test_x.py::test_a - KeyError", failed.stdout)
        empty = self.run_with_pytest_exit(5)
        self.assertEqual((empty.returncode, empty.stdout), (5, "✓ backend tests (no tests yet)\n"))


class Planning(TempDirTestCase):
    """Plan mode: one long session with larger caps, then the plan is compiled into units."""

    def test_the_planning_prompt_keeps_its_guardrails(self):
        text = (TEST_INFRA_DIR.parent / "spec-planning.md").read_text()
        for required in ("AGENTS.md", "8–20 units", "Parallel Bundles", "Rollback Plan", "exactly 5",
                         "Key files", "Spec refs"):
            self.assertIn(required, text)

    def test_plan_mode_runs_one_long_session_then_compiles(self):
        root = make_repo(self.tmp / "project")
        path = os.environ["PATH"]
        helpers.fake_claude_on_path(self.tmp / "bin")
        self.addCleanup(os.environ.__setitem__, "PATH", path)
        cr = ProjectPaths(root).cr("cr2")
        (cr.dir / "spec").mkdir(parents=True)
        cr.spec.write_text(SAMPLE_SPEC)
        (cr.dir / "prompts").mkdir()
        cr.plan_prompt.write_text("Plan {{SPEC_PATH}} into units.\n")

        def writes_the_plan(call):
            (cr.dir / "plan").mkdir(exist_ok=True)
            (cr.dir / "plan" / "cr2-implementation-plan.md").write_text(SAMPLE_PLAN)
            return agent_result(call, result_events(None))

        runner = ScriptedRunner(writes_the_plan)
        kernel = Kernel(ProjectPaths(root), Config(), runner, StartOptions(cr="cr2", allow_main=True, skip_baseline=True))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            run_plan(kernel)
        (call,) = runner.calls
        self.assertEqual((call.max_turns, call.max_budget_usd, call.model), (300, 60.0, "opus"))
        self.assertEqual(sorted(UnitStore(cr.units_dir).load()), ["U001", "U002", "U003"])
