import unittest
from pathlib import Path

from ralph.model.plan import parse_dependencies, parse_plan_text, parse_spec_refs
from ralph.model.progress import parse_legacy_progress_text, render_progress_view
from ralph.model.spec import excerpts, parse_spec_text
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from tests.helpers import SAMPLE_PLAN, SAMPLE_SPEC, TempDirTestCase


class PlanParsing(unittest.TestCase):
    def setUp(self):
        self.plan = parse_plan_text(SAMPLE_PLAN, Path("plan.md"))

    def test_finds_units_and_their_fields(self):
        self.assertEqual([u.id for u in self.plan.units], ["U001", "U002", "U003"])
        first = self.plan.unit("U001")
        self.assertEqual((first.priority, first.size, first.type, first.milestone), ("P0", "L", "api", "S1"))
        self.assertEqual(first.spend, "none")
        self.assertEqual(self.plan.unit("U003").spend, "paid")

    def test_unit_section_runs_to_the_next_level_two_heading(self):
        section = self.plan.unit("U001").section
        self.assertIn("### Acceptance Criteria", section)
        self.assertNotIn("Keyword filters", section)

    def test_dependencies_across_lines_with_notes_and_bold_ids(self):
        first = self.plan.unit("U001")
        # U018 and U025 aren't prefixed with CR1 here; the compiler decides they're external.
        self.assertEqual(first.depends_on, ["U018", "U025"])
        self.assertEqual(first.external_deps, ["cr1:U017"])
        self.assertEqual(self.plan.unit("U002").depends_on, ["U001"])
        self.assertEqual(self.plan.unit("U002").external_deps, ["cr1:U004"])

    def test_enables_is_not_a_dependency(self):
        self.assertNotIn("U003", self.plan.unit("U002").depends_on)

    def test_key_files_from_deliverables(self):
        self.assertEqual(self.plan.unit("U001").key_files, ["backend/search.py", "backend/tests/test_search.py"])

    def test_spec_refs_skip_plan_sections(self):
        self.assertEqual(self.plan.unit("U001").spec_refs, ["§4.1", "cr1:§4.2", "AC-001"])

    def test_build_order_from_the_table(self):
        self.assertEqual(self.plan.order, ["U001", "U002", "U003"])

    def test_dependency_helpers(self):
        self.assertEqual(parse_dependencies("[CR1 U023/U030, U005]"), (["U005"], ["cr1:U023", "cr1:U030"]))
        self.assertEqual(parse_spec_refs("see plan §10 and §2.3"), ["§2.3"])


class SpecExcerpts(unittest.TestCase):
    def setUp(self):
        self.spec = parse_spec_text(SAMPLE_SPEC, Path("spec.md"))

    def test_numbered_sections_include_their_subsections(self):
        self.assertIn("4.1", self.spec.sections)
        self.assertIn("Rows come back", self.spec.section("4").text)
        self.assertNotIn("acceptance", self.spec.section("4").text.lower())

    def test_excerpts_resolve_sections_and_criteria(self):
        found = excerpts(["§4.1", "AC-001"], {"cr2": self.spec}, "cr2")
        self.assertEqual(found.included, ["§4.1", "AC-001"])
        self.assertIn("The request has a query", found.text)

    def test_missing_and_oversized_refs_become_pointers(self):
        found = excerpts(["§7", "cr1:§1", "§4"], {"cr2": self.spec, "cr1": None}, "cr2", max_section_chars=20)
        self.assertEqual(found.included, [])
        self.assertEqual(len(found.pointers), 3)
        self.assertIn("too long", found.pointers[2])


class LegacyProgress(unittest.TestCase):
    TEXT = """\
# Progress
<!-- Format:
  - [ ] U0nn Not started
-->
### M0 — skeleton
- [x] U001 First
- [x] **U002 Second** — new (plan §4b)
- [!] U003 Third — blocked on the vendor
- [x] **fixes/001** — not a unit

## Unit detail
### U001 — First
It works.
### U002 — Second
Also works.
"""

    def test_reads_units_statuses_and_detail_blocks(self):
        progress = parse_legacy_progress_text(self.TEXT)
        self.assertEqual(list(progress.entries), ["U001", "U002", "U003"])
        self.assertIs(progress.entries["U003"].status, Status.BLOCKED)
        self.assertIn("It works.", progress.details["U001"])
        self.assertNotIn("Also works", progress.details["U001"])

    def test_progress_view(self):
        units = [Unit("U001", "First", "cr2", status=Status.DONE), Unit("U002", "Second", "cr2")]
        view = render_progress_view("cr2", units)
        self.assertIn("- [x] U001 First", view)
        self.assertIn("1/2 units done", view)


class Store(TempDirTestCase):
    def test_round_trip_keeps_state_and_order(self):
        store = UnitStore(self.tmp / "units")
        store.save(Unit("U002", "Two", "cr1", order=2, status=Status.IN_PROGRESS, attempts=1))
        store.save(Unit("U001", "One", "cr1", order=1))
        units = store.load()
        self.assertEqual(list(units), ["U001", "U002"])
        self.assertIs(units["U002"].status, Status.IN_PROGRESS)
        self.assertEqual(units["U002"].attempts, 1)

    def test_notes_keep_only_the_recent_lines(self):
        store = UnitStore(self.tmp / "units")
        for n in range(30):
            store.append_note("U001", f"Attempt {n}", "line a\nline b")
        self.assertLessEqual(len(store.recent_notes("U001", max_lines=10).splitlines()), 10)
        self.assertIn("Attempt 29", store.recent_notes("U001", max_lines=10))


if __name__ == "__main__":
    unittest.main()
