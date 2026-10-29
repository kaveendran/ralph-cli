import json
import unittest
from pathlib import Path

from ralph.config import load_config
from ralph.errors import RalphError
from ralph.kernel.outcome import Evidence, Verdict, judge
from ralph.runner.hooks import decide
from ralph.runner.stream import StreamStats
from ralph.verify.gates import GateResult
from ralph.verify.integrity import check_changes
from ralph.verify.review import Review
from tests.helpers import FIXTURES, TempDirTestCase

POLICY = {"root": "/repo", "frozen": ["migrations/versions/*"]}


def bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def edit(path: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": path}}


class GuardrailHook(unittest.TestCase):
    def test_denies_git_commands_that_are_the_harness_job(self):
        for command in ("git commit -m x", "git -c user.name=x commit -m y", "cd a && git push",
                        "git config user.name me", "git stash", "git branch -D old", "git pull --rebase"):
            self.assertIsNotNone(decide(bash(command), POLICY), command)

    def test_allows_reading_git(self):
        for command in ("git status", "git diff HEAD", "git log --oneline -5", "git config --get user.name",
                        "git branch", "pytest -x -q", "echo committed"):
            self.assertIsNone(decide(bash(command), POLICY), command)

    def test_protects_harness_state_and_frozen_paths(self):
        self.assertIsNotNone(decide(edit("/repo/development/cr2/units/U001.json"), POLICY))
        self.assertIsNotNone(decide(edit(".ralph/config.toml"), POLICY))
        self.assertIsNotNone(decide(edit("migrations/versions/0001.py"), POLICY))
        self.assertIsNone(decide(edit("/repo/backend/app.py"), POLICY))


class Integrity(unittest.TestCase):
    DIFF = """\
+++ b/tests/test_app.py
+@pytest.mark.skip(reason="flaky")
-    assert result == 3
+++ b/backend/app.py
-    assert x  # not a test file
"""

    def test_fails_on_deleted_tests_skip_markers_and_frozen_paths(self):
        report = check_changes([("D", "tests/test_old.py"), ("M", "migrations/versions/1.py"), ("M", "backend/app.py")],
                               self.DIFF, ["migrations/versions/*"])
        self.assertFalse(report.passed)
        self.assertEqual(len(report.failures), 3)
        self.assertEqual(report.flags, ["1 assertion line(s) removed from tests/test_app.py"])

    def test_clean_change_passes(self):
        self.assertTrue(check_changes([("A", "backend/new.py")], "+++ b/backend/new.py\n+x = 1\n", []).passed)


def gate(passed: bool, infra: bool = False) -> GateResult:
    return GateResult("unit", "pytest", passed, 0 if passed else 1, 1.0, Path("g.log"), "tail", infra_error=infra)


class Outcome(unittest.TestCase):
    def test_done_needs_every_check(self):
        self.assertIs(judge(Evidence("done", gates=[gate(True)]), 0, 3).verdict, Verdict.DONE)
        self.assertIs(judge(Evidence("done", gates=[gate(False)]), 0, 3).verdict, Verdict.RETRY)
        review = Review("changes_requested", gaps=["AC-001 untested"])
        self.assertIs(judge(Evidence("done", gates=[gate(True)], review=review), 0, 3).verdict, Verdict.RETRY)

    def test_infra_trouble_stops_without_counting(self):
        judgement = judge(Evidence("done", gates=[gate(False, infra=True)]), 0, 3)
        self.assertIs(judgement.verdict, Verdict.INFRA)
        self.assertFalse(judgement.counts_attempt)

    def test_gives_up_after_max_attempts_or_a_review_stalemate(self):
        self.assertIs(judge(Evidence("partial"), 2, 3).verdict, Verdict.BLOCKED)
        review = Review("changes_requested", gaps=["same gap"])
        stalemate = judge(Evidence("done", review=review, previous_review_gaps=["same gap"]), 0, 3)
        self.assertIs(stalemate.verdict, Verdict.BLOCKED)
        self.assertIn("stalemate", stalemate.reason)

    def test_a_capped_run_is_not_done(self):
        judgement = judge(Evidence("done", capped=True), 0, 3)
        self.assertIs(judgement.verdict, Verdict.RETRY)
        self.assertIn("cap", judgement.reason)


class Stream(unittest.TestCase):
    def test_peak_context_counts_each_message_once_and_skips_subagents(self):
        stats = StreamStats()
        usage = {"input_tokens": 10, "cache_read_input_tokens": 1000}
        stats.feed({"type": "assistant", "message": {"id": "a", "usage": usage, "content": []}})
        stats.feed({"type": "assistant", "message": {"id": "a", "usage": usage, "content": []}})
        stats.feed({"type": "assistant", "parent_tool_use_id": "t",
                    "message": {"id": "b", "usage": {"input_tokens": 999_999}, "content": []}})
        self.assertEqual(stats.peak_context, 1010)

    def test_only_back_to_back_repeats_count_as_looping(self):
        stats = StreamStats()
        call = {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -x"}}
        other = {"type": "tool_use", "name": "Edit", "input": {"file_path": "a.py"}}
        for content in ([call], [other], [call], [other], [call]):
            stats.feed({"type": "assistant", "message": {"id": str(id(content)), "content": content}})
        self.assertEqual(stats.repeated_call_max, 1)
        for n in range(3):
            stats.feed({"type": "assistant", "message": {"id": f"r{n}", "content": [call]}})
        self.assertEqual(stats.repeated_call_max, 4)

    def test_recorded_real_streams_still_parse(self):
        """Contract tests: streams recorded from Claude Code 2.1.311 in the P0 spikes."""
        streams = FIXTURES / "streams" / "2.1.311"
        structured = StreamStats()
        for line in (streams / "structured-output.jsonl").read_text().splitlines():
            structured.feed_line(line)
        self.assertEqual(structured.result["structured_output"], {"unit": "U001", "status": "done"})
        capped = StreamStats()
        for line in (streams / "max-turns.jsonl").read_text().splitlines():
            capped.feed_line(line)
        self.assertEqual(capped.result["subtype"], "error_max_turns")
        self.assertGreater(capped.tool_calls["Bash"], 0)
        denied = StreamStats()
        for line in (streams / "hook-deny.jsonl").read_text().splitlines():
            denied.feed_line(line)
        self.assertEqual(denied.summary()["permission_denials"], 1)


class Config(TempDirTestCase):
    def test_defaults_without_a_file(self):
        config = load_config(self.tmp / "missing.toml")
        self.assertEqual(config.runner.compact_window, 200_000)

    def test_unknown_keys_are_errors(self):
        path = self.tmp / "config.toml"
        path.write_text('[runner]\nmax_turn = 5\n')
        with self.assertRaises(RalphError) as raised:
            load_config(path)
        self.assertIn("max_turn", raised.exception.message)

    def test_reads_gates(self):
        path = self.tmp / "config.toml"
        path.write_text('[verify.gates]\nunit = "pytest -q"\n[verify.by_type]\ndefault = ["unit"]\n')
        self.assertEqual(load_config(path).verify.gates, {"unit": "pytest -q"})


if __name__ == "__main__":
    unittest.main()
