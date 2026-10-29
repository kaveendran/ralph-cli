import unittest
from pathlib import Path

from ralph.context.pack import PackInputs, build_pack
from ralph.errors import RalphError
from ralph.graph.compile import compile_plan, discovered_unit_id, size_issues
from ralph.graph.render import mermaid
from ralph.graph.schedule import next_unit, ready_units, waiting_counts, why_nothing_is_ready
from ralph.model.plan import parse_plan_text
from ralph.model.spec import parse_spec_text
from ralph.model.unit import Status, Unit
from tests.helpers import SAMPLE_PLAN, SAMPLE_SPEC


def units(*items: Unit) -> dict[str, Unit]:
    return {u.id: u for u in items}


class Compile(unittest.TestCase):
    def setUp(self):
        self.plan = parse_plan_text(SAMPLE_PLAN, Path("plan.md"))

    def test_unknown_dependencies_become_external(self):
        result = compile_plan(self.plan, {}, "cr2")
        first = result.units["U001"]
        self.assertEqual(first.depends_on, [])
        self.assertIn("other:U018", first.external_deps)
        self.assertEqual(result.units["U003"].depends_on, ["U001", "U002"])

    def test_recompiling_keeps_state(self):
        existing = {"U001": Unit("U001", "old title", "cr2", status=Status.DONE, handoff="note", attempts=2)}
        result = compile_plan(self.plan, existing, "cr2")
        unit = result.units["U001"]
        self.assertEqual((unit.title, unit.status, unit.handoff, unit.attempts),
                         ("Search endpoint", Status.DONE, "note", 2))
        self.assertEqual(result.added, ["U002", "U003"])

    def test_size_check(self):
        big = Unit("U009", "big", "cr2", key_files=[f"f{n}.py" for n in range(9)], size="L")
        severities = sorted(i.severity for i in size_issues(big))
        self.assertEqual(severities, ["flag", "reject"])
        big.allow_large = True
        self.assertEqual(size_issues(big), [])

    def test_discovered_ids_are_stable_and_distinct(self):
        self.assertEqual(discovered_unit_id("U037", "flaky test"), discovered_unit_id("U037", "flaky test"))
        self.assertNotEqual(discovered_unit_id("U037", "a"), discovered_unit_id("U037", "b"))
        self.assertRegex(discovered_unit_id("U037-ab12", "x"), r"^U037-[0-9a-f]{4}$")


class Schedule(unittest.TestCase):
    def test_in_progress_first_then_build_order_then_priority(self):
        graph = units(Unit("U001", "a", "c", order=2, priority="P0"), Unit("U002", "b", "c", order=1, priority="P3"),
                      Unit("U003", "c", "c", order=3, status=Status.IN_PROGRESS))
        self.assertEqual([u.id for u in ready_units(graph)], ["U003", "U002", "U001"])

    def test_dependencies_and_blocked_units(self):
        graph = units(Unit("U001", "a", "c", order=1, status=Status.BLOCKED, blocker="vendor"),
                      Unit("U002", "b", "c", order=2, depends_on=["U001"]),
                      Unit("U003", "c", "c", order=3))
        self.assertEqual(next_unit(graph).id, "U003")       # blocked U001 holds back only U002
        reasons = why_nothing_is_ready({k: v for k, v in graph.items() if k != "U003"})
        self.assertEqual(len(reasons), 2)
        self.assertIn("waits for U001 (blocked)", reasons[1])

    def test_priority_breaks_ties_then_what_unblocks_most(self):
        graph = units(Unit("U001", "a", "c"), Unit("U002", "b", "c"), Unit("U003", "c", "c", depends_on=["U002"]))
        self.assertEqual(waiting_counts(graph)["U002"], 1)
        self.assertEqual(next_unit(graph).id, "U002")

    def test_triage_units_wait_for_a_human(self):
        graph = units(Unit("U001-ab12", "found", "c", triage=True, discovered_from="U001"))
        self.assertIsNone(next_unit(graph))
        self.assertIn("triage", why_nothing_is_ready(graph)[0])

    def test_cycles_are_reported(self):
        graph = units(Unit("U001", "a", "c", depends_on=["U002"]), Unit("U002", "b", "c", depends_on=["U001"]))
        with self.assertRaises(RalphError):
            ready_units(graph)

    def test_mermaid(self):
        graph = units(Unit("U001", 'quote " title', "c", status=Status.DONE), Unit("U002", "b", "c", depends_on=["U001"]))
        diagram = mermaid(graph)
        self.assertIn("U001 --> U002", diagram)
        self.assertNotIn('quote " title', diagram)


class Pack(unittest.TestCase):
    def setUp(self):
        plan = parse_plan_text(SAMPLE_PLAN, Path("plan.md"))
        self.units = compile_plan(plan, {}, "cr2").units
        self.units["U001"].status, self.units["U001"].handoff = Status.DONE, "search() lives in backend/search.py"
        self.inputs = PackInputs(unit=self.units["U002"], units=self.units, plan=plan,
                                 specs={"cr2": parse_spec_text(SAMPLE_SPEC, Path("spec.md")), "cr1": None},
                                 instructions="Use FastAPI.", steering="Prefer small functions.",
                                 branch="work", head="abc123")

    def test_contains_only_its_own_unit(self):
        text = build_pack(self.inputs).text
        self.assertIn("Unit for this iteration: U002", text)
        self.assertIn("## Unit: Keyword filters (ID: U002)", text)
        self.assertNotIn("## Unit: CLI surface", text)
        self.assertNotIn("## Unit: Search endpoint", text)

    def test_includes_dependency_handoffs_instructions_and_steering(self):
        text = build_pack(self.inputs).text
        self.assertIn("search() lives in backend/search.py", text)
        self.assertIn("cr1:U004 (another CR", text)
        self.assertIn("Use FastAPI.", text)
        self.assertIn("Prefer small functions.", text)

    def test_tells_the_agent_which_checks_the_harness_runs(self):
        self.inputs.checks = [("unit", "python3 -m unittest discover -s tests -q")]
        self.assertIn("`python3 -m unittest discover -s tests -q` (unit)", build_pack(self.inputs).text)

    def test_stays_small_when_inputs_are_huge(self):
        self.inputs.instructions = "x" * 200_000
        self.inputs.last_attempt = "y" * 200_000
        pack = build_pack(self.inputs)
        self.assertLess(pack.chars, 20_000)
        self.assertEqual(sorted(pack.truncated), ["instructions", "last attempt"])


if __name__ == "__main__":
    unittest.main()
