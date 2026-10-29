"""Parallel lanes (P4): independent units built side by side in worktrees,
then merged one at a time. Each lane is a real `ralph start` child process,
so Claude is played by a fake `claude` executable on PATH."""

import contextlib
import io
import json
import os
import stat
import unittest

from ralph.config import load_config
from ralph.kernel.lanes import LaneRunner, overlaps, pick
from ralph.kernel.loop import Kernel, StartOptions
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.paths import ProjectPaths
from ralph.runner.cli_backend import CliRunner
from tests.helpers import TempDirTestCase, git, make_repo

FAKE_AGENT = r'''#!/usr/bin/env python3
import json, os, re, sys
from pathlib import Path
if "--version" in sys.argv:
    print("2.1.311 (Claude Code)"); sys.exit(0)
if "--help" in sys.argv:
    print("--print --output-format --model --tools --json-schema --settings"); sys.exit(0)
prompt = sys.stdin.read()
unit = re.search(r"Unit for this iteration: ([A-Za-z0-9-]+)", prompt).group(1)
for name in json.loads(os.environ.get("FAKE_FILES", "{}")).get(unit, [unit + ".txt"]):
    with open(name, "a") as handle:
        handle.write(unit + "\n")
def emit(event): print(json.dumps(event), flush=True)
emit({"type": "system", "subtype": "init", "session_id": "s", "claude_code_version": "2.1.311"})
emit({"type": "result", "subtype": "success", "num_turns": 1, "total_cost_usd": 0.01, "duration_ms": 1000,
      "structured_output": {"unit": unit, "status": "done", "summary": "did " + unit,
                            "learnings": [{"rule": "a lesson from " + unit}]}})
'''
CONFIG = """[verify]
review = "off"
[verify.gates]
unit = "true"
[loop]
converge = false
retry_delay_seconds = 0
"""
PLAN = """## Unit: A (ID: U001)
## Unit: B (ID: U002)
## Unit: C (ID: U003)
### Dependencies
- **Depends On:** [U001, U002]
"""


class Picking(unittest.TestCase):
    def test_only_units_that_cannot_touch_the_same_files_run_together(self):
        units = {"U001": Unit("U001", "A", "cr1", order=1, key_files=["backend/a.py"]),
                 "U002": Unit("U002", "B", "cr1", order=2, key_files=["backend/*.py"]),
                 "U003": Unit("U003", "C", "cr1", order=3, key_files=["frontend/c.ts"]),
                 "U004": Unit("U004", "D", "cr1", order=4),                                   # no key files
                 "U005": Unit("U005", "E", "cr1", order=5, key_files=["e.py"], wip_paths=["e.py"],
                              status=Status.IN_PROGRESS)}
        self.assertTrue(overlaps(units["U001"], units["U002"]))
        self.assertEqual([u.id for u in pick(units, 4)], ["U001", "U003"])
        self.assertEqual([u.id for u in pick(units, 4, exclude={"U001"})], ["U002", "U003"])
        self.assertEqual([u.id for u in pick(units, 1)], ["U001"])


class Lanes(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "project")
        self._env = dict(os.environ)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "claude"
        fake.write_text(FAKE_AGENT)
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"
        self.project = ProjectPaths(self.root)
        self.project.config.parent.mkdir()
        self.project.config.write_text(CONFIG)
        cr = self.project.cr("cr1")
        (cr.dir / "plan").mkdir(parents=True)
        (cr.dir / "plan" / "cr1-implementation-plan.md").write_text(PLAN)
        self.store = UnitStore(cr.units_dir)
        self.store.save(Unit("U001", "A", "cr1", order=1, key_files=["U001.txt"]))
        self.store.save(Unit("U002", "B", "cr1", order=2, key_files=["U002.txt"]))
        self.store.save(Unit("U003", "C", "cr1", order=3, key_files=["U003.txt"], depends_on=["U001", "U002"]))
        (self.root / ".gitignore").write_text("log/\n.ralph/run.lock\n.ralph/control/\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "plan, units and config")

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        super().tearDown()

    def run_lanes(self, lanes: int = 2) -> str:
        kernel = Kernel(self.project, load_config(self.project.config), CliRunner(),
                        StartOptions(cr="cr1", skip_baseline=True))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            LaneRunner(kernel, lanes, []).run()
        return out.getvalue()

    def test_independent_units_run_side_by_side_then_the_rest_in_order(self):
        output = self.run_lanes()
        self.assertIn("Lanes round 1: U001, U002", output)
        # Both lanes added a lesson to the same inbox; seen in a real run, it conflicted until the
        # inbox got a union merge (.git/info/attributes).
        self.assertIn("U002 merged from its lane", output)
        self.assertNotIn("set aside", output)
        inbox = self.project.learnings_inbox.read_text()
        self.assertIn("a lesson from U001", inbox)
        self.assertIn("a lesson from U002", inbox)
        self.assertEqual(git(self.root, "log", "-3", "--format=%s").splitlines(), ["U003: C", "U002: B", "U001: A"])
        self.assertTrue(all(u.status is Status.DONE for u in self.store.load().values()))
        self.assertEqual(len(git(self.root, "worktree", "list").splitlines()), 1)          # lanes removed
        self.assertEqual(git(self.root, "branch", "--list", "ralph/lane/*"), "")
        records = [json.loads(line) for line in self.project.iterations_log.read_text().splitlines()]
        self.assertEqual(sorted(r["unit"] for r in records), ["U001", "U002", "U003"])
        self.assertTrue((self.root / "U001.txt").exists() and (self.root / "U002.txt").exists())

    def test_a_lane_that_conflicts_is_set_aside_and_built_again(self):
        os.environ["FAKE_FILES"] = json.dumps({"U001": ["shared.txt"], "U002": ["shared.txt"]})
        output = self.run_lanes()
        self.assertIn("U002: its changes conflict with lanes merged before it (shared.txt)", output)
        self.assertTrue(git(self.root, "rev-parse", "--verify", "refs/ralph/rejected/U002/lane"))
        self.assertIn("Parallel attempt set aside", self.store.notes_path("U002").read_text())
        self.assertTrue(all(u.status is Status.DONE for u in self.store.load().values()))   # rebuilt afterwards
        self.assertEqual((self.root / "shared.txt").read_text(), "U001\nU002\n")


if __name__ == "__main__":
    unittest.main()
