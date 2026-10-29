import unittest

from ralph.commands.migrate import convert_build_prompt, plan_migration, render_config
from ralph.config import load_config
from ralph.model.unit import Status
from ralph.paths import ProjectPaths
from tests.helpers import SAMPLE_PLAN, TempDirTestCase

PROGRESS = """\
# CR2 progress
### S1 — milestone
- [x] U001 Search endpoint
- [!] U002 Keyword filters — blocked on the vendor API
- [x] **U007 Added during the build** — new
## Unit detail
### U001 — Search endpoint
Shipped; see backend/search.py.
"""

BUILD = """\
# CR2 Build Prompt

## 1. Task
- Pick the highest-priority unit whose deps are met.
- Implement fully, no stubs.

## 3. Progress tracking
Maintain the progress file.

## 5. Commit cadence
1. `git add -A && git commit -m "..."`

## 8. Special notes
- The impl lives in `backend/`.
"""


class Migrate(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.project = ProjectPaths(self.tmp)
        cr = self.project.cr("cr2")
        for path, text in ((cr.dir / "plan" / "cr2-implementation-plan.md", SAMPLE_PLAN),
                           (cr.legacy_progress, PROGRESS), (cr.legacy_build_prompt, BUILD)):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.tmp / "scripts").mkdir()
        (self.tmp / "scripts" / "test-unit.sh").write_text("pytest\n")
        (self.tmp / "scripts" / "test-paid.sh").write_text("pytest -m paid\n")
        self.result = plan_migration(self.project, cr)

    def test_statuses_come_from_the_old_checklist(self):
        units = self.result["units"]
        self.assertIs(units["U001"].status, Status.DONE)
        self.assertIs(units["U002"].status, Status.BLOCKED)
        self.assertIn("vendor", units["U002"].blocker)
        self.assertIs(units["U003"].status, Status.TODO)

    def test_units_only_in_the_checklist_are_kept(self):
        self.assertIn("U007", self.result["units"])
        self.assertIn("no entry", self.result["units"]["U007"].description)

    def test_detail_blocks_become_notes(self):
        self.assertIn("backend/search.py", self.result["notes"]["U001"])

    def test_build_prompt_loses_what_the_harness_now_does(self):
        instructions = self.result["instructions"]
        self.assertIn("The impl lives in `backend/`.", instructions)
        self.assertIn("Implement fully", instructions)
        self.assertNotIn("git add -A", instructions)
        self.assertNotIn("Maintain the progress file", instructions)
        self.assertNotIn("highest-priority", instructions)
        self.assertEqual(len(self.result["removed"]), 3)

    def test_config_detects_gates_and_parses(self):
        path = self.tmp / "config.toml"
        path.write_text(self.result["config"])
        config = load_config(path)
        self.assertEqual(config.verify.gates, {"unit": "bash scripts/test-unit.sh", "paid": "bash scripts/test-paid.sh"})
        self.assertEqual(config.verify.paid_gates, ["paid"])
        self.assertEqual(config.verify.by_type["default"], ["unit"])

    def test_gitignore_additions(self):
        self.assertEqual(self.result["gitignore_add"], ["log/", ".ralph/run.lock", ".ralph/control/"])

    def test_convert_keeps_unknown_sections(self):
        text, removed = convert_build_prompt("# T\n\n## 0. Sources of truth\n- impl at /x\n", "cr1")
        self.assertIn("impl at /x", text)
        self.assertEqual(removed, [])

    def test_render_config_without_scripts(self):
        self.assertIn("[verify.gates]", render_config({}))


if __name__ == "__main__":
    unittest.main()
