"""The review and delivery workflows (P5): critique, refine-plan, retro, deploy,
and the pieces they share. Claude is played by a PromptRunner that answers by
prompt content, because the parallel calls arrive in any order."""

import contextlib
import io
import os
import stat
import sys
import threading
import time
import unittest
from unittest import mock

from ralph.config import Config, TelemetryConfig
from ralph.errors import RalphError
from ralph.model.plan import parse_plan_text
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.monitor.otel import otel_env
from ralph.paths import ProjectPaths
from ralph.runner.base import AgentCall
from ralph.runner.cli_backend import CliRunner
from ralph.team.ledger import new_entry, write_entry
from ralph.workflows import critique, deploy, inputs, retro
from ralph.workflows.council import Council, Expert
from ralph.workflows.plan_edit import apply_edits
from ralph.workflows.report import count_items, severities, split_at, todo_count
from ralph.workflows.workspace import Workspace
from tests.helpers import SAMPLE_PLAN, SAMPLE_SPEC, PromptRunner, TempDirTestCase, git, make_repo

CRITICAL_REPORT = """## Ralph Critique Report — cr2

### CRITICAL — Fix before starting build
1. U002 has no test plan. Add one. [origin:tester]

### IMPORTANT — Fix before starting build
None.

### SUGGESTED — Consider before starting
1. Name the env vars. [origin:docs]

### Plan health
Fine otherwise.
"""
CLEAN_REPORT = CRITICAL_REPORT.replace("1. U002 has no test plan. Add one. [origin:tester]", "None.")
CRITIQUE_EXPERTS = {"senior software architect": "## Architect Review\nU001 is fine.",
                    "senior test engineer reviewing": "## Test Engineer Review\nU002 lacks tests.",
                    "senior risk analyst": "## Risk Analyst Review\nNone identified",
                    "Your focus: implementation correctness": "## Domain Expert Review\nUse async.",
                    "application security engineer": "## Security Reviewer\nNone identified",
                    "senior performance engineer": "## Performance Reviewer\nNone identified",
                    "senior technical writer": "## Documentation Reviewer\nNone identified"}


def silence():
    """Capture what the workflow prints; `with silence() as out:` gives it back."""
    return _Quiet()


class _Quiet:
    def __init__(self):
        self.out = io.StringIO()
        self.stack = contextlib.ExitStack()

    def __enter__(self):
        self.stack.enter_context(contextlib.redirect_stdout(self.out))
        self.stack.enter_context(contextlib.redirect_stderr(self.out))
        return self.out

    def __exit__(self, *exc):
        return self.stack.__exit__(*exc)


class Reports(unittest.TestCase):
    def test_severities_under_either_heading_level(self):
        self.assertEqual(severities(CRITICAL_REPORT), {"CRITICAL": 1, "IMPORTANT": 0, "SUGGESTED": 1})
        deploy_report = ("# Deploy Critique Report\n\n## Verdict\nNO-GO\n\n## CRITICAL — fix before deploy\n"
                         "1. a\n2. b\n   detail\n### a sub-heading inside\n3. c\n\n## IMPORTANT — fix before deploy\n"
                         "None.\n")
        self.assertEqual(count_items(deploy_report, "CRITICAL"), 3)
        self.assertEqual(count_items(deploy_report, "IMPORTANT"), 0)

    def test_split_keeps_a_parts_own_headings(self):
        text = ("# R\n\n## Summary\nShipped.\n\n## RELEASE_NOTES draft\n### Features\n- x\n\n"
                "## PR Description draft\n## Summary\nBody\n## Testing\nAll green\n")
        parts = split_at(text, ["## Summary", "## RELEASE_NOTES draft", "## PR Description draft"])
        self.assertEqual(parts["## RELEASE_NOTES draft"], "### Features\n- x")
        self.assertEqual(parts["## PR Description draft"], "## Summary\nBody\n## Testing\nAll green")
        self.assertEqual(todo_count(["<TODO: a> and <TODO: b>", "<TODO: c>"]), 3)


class PlanEdits(unittest.TestCase):
    def test_untouched_text_stays_byte_for_byte(self):
        self.assertEqual(apply_edits(SAMPLE_PLAN, {}), SAMPLE_PLAN)
        revised = apply_edits(SAMPLE_PLAN, {"units": [{"unit": "U002", "section": "## Unit: Keyword filters (ID: U002)\n"
                                                                                   "Now with a test plan."}]})
        before, after = parse_plan_text(SAMPLE_PLAN, None), parse_plan_text(revised, None)
        self.assertEqual(after.unit("U001").section, before.unit("U001").section)
        self.assertEqual(after.unit("U003").section, before.unit("U003").section)
        self.assertIn("Now with a test plan.", after.unit("U002").section)

    def test_new_units_sections_and_questions(self):
        revised = apply_edits(SAMPLE_PLAN, {
            "new_units": [{"section": "## Unit: Export (ID: U004)\nExport rows."}],
            "sections": [{"heading": "## 10. Implementation Order", "text": "| # | Unit |\n|---|---|\n| 4 | U004 |"}],
            "open_questions": ["Which auth provider?"]})
        plan = parse_plan_text(revised, None)
        self.assertEqual([u.id for u in plan.units], ["U001", "U002", "U003", "U004"])
        self.assertEqual(plan.order, ["U004"])
        self.assertTrue(revised.rstrip().endswith("## Open Questions\n\n- Which auth provider?"))

    def test_edits_that_would_damage_the_plan_are_refused(self):
        bad = [
            {"units": [{"unit": "U009", "section": "## Unit: X (ID: U009)"}]},
            {"units": [{"unit": "U002", "section": "Keyword filters, now better"}]},
            {"units": [{"unit": "U002", "section": "## Unit: Renamed (ID: U002)\nx"}]},
            {"new_units": [{"section": "## Unit: Again (ID: U001)\nx"}]},
            {"sections": [{"heading": "## 99. Nowhere", "text": "x"}]},
        ]
        for edits in bad:
            with self.assertRaises(RalphError, msg=edits):
                apply_edits(SAMPLE_PLAN, edits)
        kept = apply_edits(SAMPLE_PLAN, {"units": [{"unit": "U002", "section": "## Unit: Keyword filters (deprecated) "
                                                                             "(ID: U002)\nDeprecated: merged into U001."}]})
        self.assertIn("Deprecated: merged into U001.", kept)


class Inputs(TempDirTestCase):
    def test_a_long_plan_becomes_an_outline_with_pointers(self):
        body = "".join(f"## Unit: Unit {n} (ID: U{n:03d})\n" + "detail line\n" * 400 for n in range(1, 31))
        path = self.tmp / "plan.md"
        path.write_text(body)
        plan = parse_plan_text(body, path)
        text = inputs.plan_text(plan, limit=20_000)
        self.assertLessEqual(len(text), 20_100)
        self.assertIn("so this is an outline", text)
        self.assertIn("## Unit: Unit 30 (ID: U030)", text)
        self.assertRegex(text, r"\[… cut; the rest is in plan\.md from line \d+\]")
        small = self.tmp / "small.md"
        small.write_text(SAMPLE_PLAN)
        self.assertEqual(inputs.plan_text(parse_plan_text(SAMPLE_PLAN, small)), SAMPLE_PLAN)   # fits: verbatim

    def test_unit_table_trouble_and_rollbacks(self):
        store = UnitStore(self.tmp / "units")
        units = {"U001": Unit("U001", "Search", "cr2", status=Status.BLOCKED, blocker="no API key", attempts=3),
                 "U002": Unit("U002", "Filters", "cr2", status=Status.DONE, discovered_from="U001")}
        store.save_all(units.values())
        store.append_note("U001", "Attempt 1: retry", "Gate unit failed: KeyError in search.py")
        table = inputs.unit_table(units, {"U001": {"iterations": 3, "minutes": 42, "cost_usd": 5.5}})
        self.assertIn("| U001 | Search | - | blocked | 3 |  | plan | 3 | 42 | 5.50 |", table)
        self.assertIn("found in U001", table)
        trouble = inputs.trouble(units, store, "cr2")
        self.assertIn("### cr2:U001 Search — blocked, 3 attempt(s)\nBlocker: no API key", trouble)
        self.assertIn("KeyError in search.py", trouble)
        plan = parse_plan_text("## Unit: A (ID: U001)\n### Rollback Plan\nDrop the column.\n### Tests\nx\n"
                               "## Unit: B (ID: U002)\nNo rollback.\n", self.tmp / "p.md")
        rollbacks = inputs.rollback_sections(plan)
        self.assertIn("### U001 — A\nDrop the column.", rollbacks)
        self.assertIn("### U002 — B\n(no Rollback Plan section)", rollbacks)


class CouncilRuns(TempDirTestCase):
    def test_at_most_four_at_once_and_an_empty_expert_is_left_out(self):
        runner = PromptRunner(*[(f"expert {n}", f"review {n}") for n in range(7)], ("expert empty", ""), delay=0.4)
        council = Council(runner, self.tmp, self.tmp / "run")
        experts = [Expert(f"e{n}", f"expert {n}") for n in range(7)] + [Expert("quiet", "expert empty")]
        with silence() as out:
            opinions = council.ask(experts)
        self.assertLessEqual(runner.most_at_once, 4)
        self.assertGreater(runner.most_at_once, 1)
        self.assertEqual(list(opinions), [f"e{n}" for n in range(7)] + ["quiet"])
        self.assertEqual(opinions["quiet"].problem, "returned nothing (success)")
        self.assertIn("quiet returned nothing", out.getvalue())
        self.assertTrue(all(c.tools == ["Read", "Grep", "Glob"] for c in runner.calls))
        self.assertTrue(all(not c.skip_permissions for c in runner.calls))
        self.assertEqual(len({id(c.cancel) for c in runner.calls}), 1)          # one shared cancel signal

    def test_no_reviews_at_all_is_an_error(self):
        council = Council(PromptRunner(("x", "")), self.tmp, self.tmp / "run")
        with silence(), self.assertRaises(RalphError):
            council.ask([Expert("a", "x"), Expert("b", "x")])

    def test_a_cancelled_call_stops_the_real_process(self):
        fake = self.tmp / "claude"
        fake.write_text("#!/bin/sh\necho '{\"type\":\"system\",\"subtype\":\"init\"}'\nexec sleep 30\n")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        cancel = threading.Event()
        call = AgentCall(prompt="x", cwd=self.tmp, model="haiku", log_path=self.tmp / "s.jsonl", cancel=cancel)
        threading.Timer(0.5, cancel.set).start()
        started = time.monotonic()
        result = CliRunner(str(fake)).run(call)
        self.assertEqual(result.stopped_by, "interrupted")
        self.assertLess(time.monotonic() - started, 20)


class Telemetry(unittest.TestCase):
    def test_off_by_default_and_safe_attribute_values(self):
        self.assertEqual(otel_env(TelemetryConfig()), {})
        env = otel_env(TelemetryConfig(otlp_endpoint="http://localhost:4317", team="payments"),
                       developer="Ada Lovelace", cr="cr2")
        self.assertEqual(env["CLAUDE_CODE_ENABLE_TELEMETRY"], "1")
        self.assertEqual(env["OTEL_EXPORTER_OTLP_PROTOCOL"], "grpc")
        self.assertEqual(env["OTEL_RESOURCE_ATTRIBUTES"], "team.id=payments,developer=Ada%20Lovelace,ralph.cr=cr2")


class WorkflowCase(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "project")
        self.project = ProjectPaths(self.root)
        self.cr = self.project.cr("cr2")
        (self.cr.dir / "plan").mkdir(parents=True)
        (self.cr.dir / "spec").mkdir(parents=True)
        (self.cr.dir / "plan" / "cr2-implementation-plan.md").write_text(SAMPLE_PLAN)
        self.cr.spec.write_text(SAMPLE_SPEC)
        self.ws = Workspace(self.project, Config(), self.cr)

    def council(self, *rules) -> tuple[Council, PromptRunner]:
        runner = PromptRunner(*rules)
        return Council(runner, self.root, self.tmp / "run"), runner


class PlanCritique(WorkflowCase):
    def test_the_report_merges_every_expert_and_is_saved(self):
        (self.root / "requirements.txt").write_text("fastapi==0.115.0\n")
        council, runner = self.council(("synthesizing expert reviews", CRITICAL_REPORT),
                                       *CRITIQUE_EXPERTS.items())
        with silence():
            outcome = critique.critique(self.ws, council)
        self.assertEqual(outcome.path.parent, self.cr.dir / "critique")
        self.assertEqual(outcome.path.read_text(), CRITICAL_REPORT)
        self.assertAlmostEqual(outcome.cost_usd, 0.08)
        (lead,) = runner.prompts("synthesizing expert reviews")
        for text in CRITIQUE_EXPERTS.values():
            self.assertIn(text, lead)
        self.assertIn("no docker-compose.test.yml, so there's no infrastructure review", lead)
        (domain,) = runner.prompts("Your focus")
        self.assertIn("tech stack (fastapi)", domain)
        self.assertIn("1. API design", domain)
        architect = runner.prompts("senior software architect")[0]
        self.assertIn("## Unit: Search endpoint (ID: U001)", architect)
        self.assertIn("AC-001 WHEN a query arrives, THE SYSTEM SHALL return rows.", architect)   # the spec's criteria

    def test_refine_plan_applies_approved_edits_and_stops_when_clean(self):
        edits = {"units": [{"unit": "U002", "section": "## Unit: Keyword filters (ID: U002)\n\n### Test Plan\n- filters"}]}
        council, runner = self.council(("synthesizing expert reviews", [CRITICAL_REPORT, CLEAN_REPORT]),
                                       ("revising an implementation plan", edits), *CRITIQUE_EXPERTS.items())
        with silence() as out:
            critique.refine_plan(self.ws, council, max_iterations=3, gate="auto", scope="critical,important")
        plan_text = self.cr.plan.read_text()
        self.assertIn("### Test Plan\n- filters", plan_text)
        revisions = self.cr.plan.parent / "revisions"
        self.assertEqual(sorted(p.name for p in revisions.iterdir()), ["v0-initial.md", "v1-candidate.md", "v1.md"])
        self.assertEqual((revisions / "v0-initial.md").read_text(), SAMPLE_PLAN)
        self.assertIn("Nothing CRITICAL or IMPORTANT is left", out.getvalue())
        (editor,) = runner.prompts("revising an implementation plan")
        self.assertIn("UNITS THE CRITIQUE CITES:\n## Unit: Keyword filters (ID: U002)", editor)
        self.assertIn("next free ID after U003", editor)

    def test_refine_plan_asks_and_quit_changes_nothing(self):
        edits = {"open_questions": ["Which index?"]}
        council, _ = self.council(("synthesizing expert reviews", CRITICAL_REPORT),
                                  ("revising an implementation plan", edits), *CRITIQUE_EXPERTS.items())
        with silence(), mock.patch.object(sys, "stdin", io.StringIO("4\n")):
            critique.refine_plan(self.ws, council, max_iterations=3, gate="human", scope="critical")
        self.assertEqual(self.cr.plan.read_text(), SAMPLE_PLAN)


class Retrospective(WorkflowCase):
    def test_release_notes_and_the_whole_pr_body_are_saved(self):
        store = UnitStore(self.cr.units_dir)
        store.save(Unit("U001", "Search endpoint", "cr2", status=Status.DONE))
        store.save(Unit("U002", "Keyword filters", "cr2", status=Status.BLOCKED, blocker="no index", attempts=3))
        write_entry(self.root, new_entry("U001", "cr2", "Search endpoint", "A", "a@x", "", [("A", "backend/tests/test_search.py")],
                                         [], "", []))
        lead = ("# cr2 Retrospective\n\n## Summary\nShipped U001.\n\n## Plan vs Reality\nx\n\n## Blocker Patterns\ny\n\n"
                "## Test Coverage Gaps\nz\n\n## Action Items Before Release\n1. MUST-FIX: U002\n\n"
                "## RELEASE_NOTES draft\n### Features\n- Search (U001)\n\n"
                "## PR Description draft\n## Summary\nSearch.\n## Testing\nUnit tests.\n## Risk & Rollback\nLow.\n")
        council, runner = self.council(("writing the retrospective for", lead),
                                       ("post-build retrospective", "## Plan-vs-Reality\nok"),
                                       ("analysing blocker patterns", "## Blocker Patterns\nindex"),
                                       ("auditing whether the SHIPPED", "## Test Coverage Gaps\nok"))
        with silence():
            outcome, written = retro.retro(self.ws, council)
        folder = self.cr.dir / "retro"
        self.assertEqual([p.name for p in written], ["RELEASE_NOTES.md", "PR_DESCRIPTION.md"])
        self.assertEqual((folder / "PR_DESCRIPTION.md").read_text(),
                         "## Summary\nSearch.\n## Testing\nUnit tests.\n## Risk & Rollback\nLow.\n")
        self.assertEqual((folder / "RELEASE_NOTES.md").read_text(), "### Features\n- Search (U001)\n")
        self.assertIn("| U002 | Keyword filters | - | blocked | 3 |", runner.prompts("post-build retrospective")[0])
        self.assertIn("Blocker: no index", runner.prompts("analysing blocker patterns")[0])
        self.assertIn("U001 Search endpoint: backend/tests/test_search.py", runner.prompts("auditing whether")[0])
        self.assertEqual(runner.calls[[i for i, c in enumerate(runner.calls)
                                       if "blocker patterns" in c.prompt][0]].tools, [])


class Deployment(WorkflowCase):
    SCAFFOLDS = {"DEPLOYMENT PLAN scaffold": {"document": "# Deployment Plan: cr2\n\n## Overview\n- U001\n\n"
                                                           "## Environment\n| cluster | <TODO: name> |\n"},
                 "DEPLOYMENT RUNBOOK": {"document": "# Runbook\n<TODO: owner>"},
                 "ROLLBACK PROCEDURES document": {"document": "# Rollback\n### U001 — Search endpoint"},
                 "POST-DEPLOY VERIFICATION": {"document": "# Verification\nAC-001: <TODO: host>"}}
    EXPERTS = {"database migration engineer": "## Migration Expert\nSAFE", "feature flags and progressive": "## FF\nSAFE",
               "release engineer": "## Rollout\nSAFE", "senior SRE": "## Observability\nSAFE",
               "incident-response engineer": "## Rollback\nSAFE", "governance and compliance": "## Compliance\nSAFE"}

    def test_scaffold_writes_four_documents_and_counts_the_todos(self):
        council, runner = self.council(*self.SCAFFOLDS.items())
        with silence():
            written, todos, _ = deploy.scaffold(self.ws, council)
        self.assertEqual(sorted(p.name for p in written), ["deploy-plan.md", "rollback.md", "runbook.md", "verify.md"])
        self.assertEqual(todos, 3)
        rollback_prompt = runner.prompts("ROLLBACK PROCEDURES document")[0]
        self.assertIn("ROLLBACK PLAN SECTIONS (verbatim, per unit):\n### U001 — Search endpoint", rollback_prompt)
        self.assertIn("AC-001", runner.prompts("POST-DEPLOY VERIFICATION")[0])
        self.assertTrue(all(c.json_schema and "document" in c.json_schema["properties"] for c in runner.calls))
        with self.assertRaises(RalphError):
            deploy.scaffold(self.ws, council)

    def test_critique_with_peer_review_then_refine(self):
        council, _ = self.council(*self.SCAFFOLDS.items())
        with silence():
            deploy.scaffold(self.ws, council)
        report = "# Deploy Critique Report — cr2\n\n## Verdict\nNO-GO\n\n## CRITICAL — fix before deploy\n1. U001 gate\n\n" \
                 "## IMPORTANT — fix before deploy\nNone.\n"
        clean = report.replace("1. U001 gate", "None.")
        plan_before = (self.cr.dir / "deploy" / "deploy-plan.md").read_text()
        revised = plan_before.replace("## Overview\n- U001", "## Overview\n- U001, gated by its smoke test")
        council, runner = self.council(("deploy-critique synthesizer", [report, clean]),
                                       ("coordinating a peer-review pass", "AGREEMENTS: none"),
                                       ("revising a deploy plan", {"document": revised}), *self.EXPERTS.items())
        with silence():
            outcome = deploy.critique(self.ws, council)
        self.assertIn("NO-GO", outcome.path.read_text())
        self.assertIn("AGREEMENTS: none", runner.prompts("deploy-critique synthesizer")[0])
        self.assertIn("There are 3 of them", runner.prompts("deploy-critique synthesizer")[0])
        with silence():
            deploy.refine_deploy(self.ws, council, max_iterations=3, gate="auto", scope="critical,important")
        self.assertIn("gated by its smoke test", (self.cr.dir / "deploy" / "deploy-plan.md").read_text())
        self.assertEqual(len(runner.prompts("coordinating a peer-review pass")), 1)   # refine skips peer review

    def test_a_revision_that_loses_content_is_refused(self):
        before = "# Plan\n\n## Overview\n- U001\n- U002\n\n## Rollout\n" + "detail\n" * 50
        with self.assertRaises(RalphError):
            deploy.check_revision(before, before.replace("- U002\n", ""))
        with self.assertRaises(RalphError):
            deploy.check_revision(before, before.replace("## Rollout", "Rollout"))
        with self.assertRaises(RalphError):
            deploy.check_revision(before, before[:120])
        deploy.check_revision(before, before + "\n## Open Questions\n- none\n")


if __name__ == "__main__":
    unittest.main()
