"""Project memory: lessons, promotion, size budgets and decay."""

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

from ralph import frontmatter
from ralph.cli import main
from ralph.config import MemoryConfig
from ralph.memory.decay import archive_reviewed, candidates, decay
from ralph.memory.learnings import Learning, group, read_inbox, relevant, similar, write_inbox
from ralph.memory.lint import lint_memory
from ralph.memory.promote import apply, area_of, propose
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.paths import ProjectPaths
from tests.helpers import TempDirTestCase, git, make_repo


class Frontmatter(unittest.TestCase):
    def test_round_trip_of_the_subset_skills_and_rules_use(self):
        meta = {"name": "api-conventions", "description": "Writes endpoints: routers, schemas.",
                "paths": ["backend/**", "api/*.py"], "ralph-versions": {"fastapi": "0.115"}}
        text = frontmatter.render(meta) + "\n# Body\n"
        parsed, body = frontmatter.split(text)
        self.assertEqual(parsed, meta)
        self.assertEqual(body, "# Body\n")

    def test_inline_lists_quotes_and_no_frontmatter(self):
        parsed, _ = frontmatter.split('---\npaths: [backend/**, "api/*.py"]\nname: \'x\'\n---\nbody')
        self.assertEqual(parsed, {"paths": ["backend/**", "api/*.py"], "name": "x"})
        self.assertEqual(frontmatter.split("# just markdown\n"), ({}, "# just markdown\n"))


class Lessons(unittest.TestCase):
    def test_the_same_lesson_in_other_words_groups_and_recurring_ones_come_first(self):
        items = [
            Learning("Use the session fixture for database tests", unit="U001", at="1"),
            Learning("Run alembic upgrade before the api tests", unit="U002", at="2"),
            Learning("use the session fixture for the database tests", unit="U003", at="3"),
        ]
        self.assertTrue(similar(items[0].rule, items[2].rule))
        groups = group(items)
        self.assertEqual([g.units for g in groups], [["U001", "U003"], ["U002"]])

    def test_relevant_lessons_prefer_the_units_area_and_skip_reviewed_ones(self):
        items = [
            Learning("General lesson", at="1"),
            Learning("Backend lesson", paths=["backend/**"], at="2"),
            Learning("Frontend lesson", paths=["frontend/**"], at="3"),
            Learning("Promoted lesson", paths=["backend/**"], status="promoted", at="4"),
        ]
        picked = [i.rule for i in relevant(items, ["backend/search.py"], limit=5)]
        self.assertEqual(picked, ["Backend lesson", "General lesson"])
        self.assertEqual(len(relevant(items, ["backend/x.py"], limit=1)), 1)



class Inbox(TempDirTestCase):
    def test_round_trip_skips_bad_lines_and_unknown_fields(self):
        inbox = self.tmp / "inbox.jsonl"
        write_inbox(inbox, [Learning("Keep it", paths=["a/**"], unit="U001")])
        with open(inbox, "a") as handle:
            handle.write('not json\n{"trigger": "no rule"}\n{"rule": "Extra field", "severity": "high"}\n')
        self.assertEqual([(i.rule, i.paths) for i in read_inbox(inbox)], [("Keep it", ["a/**"]), ("Extra field", [])])
        self.assertEqual(read_inbox(self.tmp / "missing.jsonl"), [])


class Promotion(TempDirTestCase):
    def lesson(self, rule: str, paths=()) -> object:
        return group([Learning(rule, paths=list(paths), unit="U001")])[0]

    def test_where_each_kind_of_lesson_goes(self):
        self.assertEqual(propose(self.lesson("Never import from app.main in tests"), self.tmp).target, "claude_md")
        rule = propose(self.lesson("Use the async session", ["backend/db/*.py"]), self.tmp)
        self.assertEqual((rule.target, rule.path.name), ("rule", "backend.md"))
        skill = propose(self.lesson("To add a migration:\n1. run alembic revision\n2. edit it\n3. upgrade"), self.tmp)
        self.assertEqual(skill.target, "skill")
        self.assertEqual(area_of(["backend/a.py", "frontend/b.ts"]), "project")

    def test_claude_md_gets_the_rule_under_invariants_once(self):
        claude_md = self.tmp / "CLAUDE.md"
        claude_md.write_text("# Map\n\n## Invariants\n- one\n\n## Commands\n- make test\n")
        proposal = propose(self.lesson("Money is stored in cents"), self.tmp)
        apply(proposal)
        apply(proposal)
        self.assertEqual(claude_md.read_text(),
                         "# Map\n\n## Invariants\n- one\n- Money is stored in cents\n\n## Commands\n- make test\n")

    def test_a_new_rule_file_is_scoped_to_the_lessons_paths(self):
        lesson = self.lesson("Use the async session", ["backend/db/*.py"])
        apply(propose(lesson, self.tmp), paths_for_rule=lesson.paths)
        meta, body = frontmatter.split((self.tmp / ".claude" / "rules" / "backend.md").read_text())
        self.assertEqual(meta, {"paths": ["backend/db/*.py"]})
        self.assertIn("- Use the async session", body)

    def test_a_skill_is_never_overwritten(self):
        proposal = propose(self.lesson("To release:\n1. tag\n2. push the tag"), self.tmp)
        proposal.path.parent.mkdir(parents=True)
        proposal.path.write_text("mine\n")
        apply(proposal)
        self.assertEqual(proposal.path.read_text(), "mine\n")


class Budgets(TempDirTestCase):
    def test_files_over_budget_are_reported(self):
        (self.tmp / "CLAUDE.md").write_text("line\n" * 201)
        rules = self.tmp / ".claude" / "rules"
        rules.mkdir(parents=True)
        (rules / "everywhere.md").write_text("x\n" * 51)
        (rules / "small.md").write_text("---\npaths: [a/**]\n---\nx\n")
        units = self.tmp / "units"
        units.mkdir()
        (units / "U001.notes.md").write_text("n\n" * 201)
        found = {f.path.name: f.message for f in lint_memory(self.tmp, MemoryConfig(), [units])}
        self.assertEqual(set(found), {"CLAUDE.md", "everywhere.md", "U001.notes.md"})
        self.assertIn("in every session", found["everywhere.md"])

    def test_a_long_inbox_asks_for_a_review(self):
        inbox = self.tmp / "inbox.jsonl"
        write_inbox(inbox, [Learning(f"lesson {n}") for n in range(31)])
        self.assertIn("ralph memory review", lint_memory(self.tmp, MemoryConfig(), [], inbox)[0].message)


class Decay(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.store = UnitStore(self.tmp / "units")
        for n in range(1, 5):
            unit = Unit(f"U00{n}", f"Unit {n}", "cr1", status=Status.DONE, handoff=f"Built thing {n}.\nMore.",
                        done={"at": f"2026-10-0{n}T00:00:00Z"})
            self.store.save(unit)
            self.store.notes_path(unit.id).write_text("old note\n" * 20)
        self.units = self.store.load()

    def test_only_old_long_notes_decay_and_their_text_is_archived(self):
        self.assertEqual([u.id for u in candidates(self.units, self.store, keep_recent=2)], ["U001", "U002"])
        archive = self.tmp / "archive"
        self.assertEqual(decay(self.units, self.store, archive, self.tmp, keep_recent=2, dry_run=True), ["U001", "U002"])
        self.assertFalse(archive.exists())
        decay(self.units, self.store, archive, self.tmp, keep_recent=2)
        self.assertEqual((archive / "notes" / "U001.notes.md").read_text(), "old note\n" * 20)
        summary = self.store.notes_path("U001").read_text()
        self.assertIn("Done on 2026-10-01: Built thing 1.", summary)
        self.assertIn("archive/notes/U001.notes.md", summary)
        self.assertEqual(self.store.notes_path("U004").read_text(), "old note\n" * 20)

    def test_reviewed_lessons_move_to_the_archive(self):
        inbox = self.tmp / "inbox.jsonl"
        write_inbox(inbox, [Learning("a"), Learning("b", status="promoted"), Learning("c", status="dismissed")])
        self.assertEqual(archive_reviewed(inbox), 2)
        self.assertEqual([i.rule for i in read_inbox(inbox)], ["a"])
        self.assertEqual([i.rule for i in read_inbox(inbox.with_name("archive.jsonl"))], ["b", "c"])


class MemoryCommands(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "project")
        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.project = ProjectPaths(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        super().tearDown()

    def ralph(self, *args: str, answers: str = "") -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch.object(sys, "stdin", io.StringIO(answers)):
            code = main(list(args))
        return code, out.getvalue()

    def test_review_applies_dismisses_and_remembers_each_answer(self):
        write_inbox(self.project.learnings_inbox, [
            Learning("Money is stored in cents", unit="U001"),
            Learning("money is stored in cents", unit="U004"),
            Learning("Use the async session", paths=["backend/db/*.py"], unit="U002"),
            Learning("Prefer tabs", unit="U003"),
        ])
        code, output = self.ralph("memory", "review", answers="a\nd\n")    # the third: no answer → skip
        self.assertEqual(code, 0)
        self.assertIn("from U001, U004", output)
        self.assertIn("- Money is stored in cents", (self.root / "CLAUDE.md").read_text())
        statuses = [(i.rule, i.status, i.promoted_to) for i in read_inbox(self.project.learnings_inbox)]
        self.assertEqual(statuses, [("Money is stored in cents", "promoted", "CLAUDE.md"),
                                    ("money is stored in cents", "promoted", "CLAUDE.md"),
                                    ("Use the async session", "dismissed", ""),
                                    ("Prefer tabs", "new", "")])
        self.assertEqual(git(self.root, "status", "--porcelain", "CLAUDE.md"), "?? CLAUDE.md")   # left for you

    def test_review_list_changes_nothing(self):
        write_inbox(self.project.learnings_inbox, [Learning("Money is stored in cents", unit="U001")])
        code, output = self.ralph("memory", "review", "--list")
        self.assertIn("→ CLAUDE.md", output)
        self.assertFalse((self.root / "CLAUDE.md").exists())
        self.assertEqual(read_inbox(self.project.learnings_inbox)[0].status, "new")

    def test_lint_strict_fails_over_budget(self):
        (self.root / "CLAUDE.md").write_text("x\n" * 300)
        self.assertEqual(self.ralph("memory", "lint")[0], 0)
        code, output = self.ralph("memory", "lint", "--strict")
        self.assertEqual(code, 1)
        self.assertIn("CLAUDE.md: 300 lines (budget 200)", output)

    def test_decay_commits_the_summaries_and_the_archive(self):
        cr = self.project.cr("cr1")
        store = UnitStore(cr.units_dir)
        for n in range(1, 4):
            store.save(Unit(f"U00{n}", f"Unit {n}", "cr1", status=Status.DONE, handoff="Did it.",
                            done={"at": f"2026-10-0{n}"}))
            store.notes_path(f"U00{n}").write_text("note\n" * 15)
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "units")
        code, output = self.ralph("memory", "decay", "--cr", "1", "--keep", "1")
        self.assertEqual(code, 0, output)
        self.assertIn("U001, U002", output)
        self.assertEqual(git(self.root, "log", "-1", "--format=%s"), "ralph: memory decay for cr1")
        self.assertEqual(git(self.root, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
