import json
import stat
import time
import unittest

from ralph.runner.base import AgentCall
from ralph.runner.cli_backend import CliRunner, build_command
from ralph.vcs import commit as vcs
from ralph.vcs.git import Git
from tests.helpers import TempDirTestCase, git, make_repo


class OwnPathCommits(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.root = make_repo(self.tmp / "repo")
        self.git = Git(self.root)
        (self.root / "mine.txt").write_text("my uncommitted work\n")       # dirty before the run

    def test_commits_only_what_the_run_changed(self):
        snapshot = vcs.take_snapshot(self.git)
        (self.root / "new.py").write_text("x = 1\n")
        changes = vcs.run_changes(self.git, snapshot)
        self.assertEqual(changes.paths, ["new.py"])
        vcs.commit_paths(self.git, changes.paths, "U001: thing")
        self.assertEqual(git(self.root, "show", "--name-only", "--format=", "HEAD"), "new.py")
        self.assertIn("mine.txt", git(self.root, "status", "--porcelain"))

    def test_reports_overlap_with_your_uncommitted_files(self):
        snapshot = vcs.take_snapshot(self.git)
        (self.root / "mine.txt").write_text("my work, then the agent's\n")
        changes = vcs.run_changes(self.git, snapshot)
        self.assertEqual(changes.overlapping, ["mine.txt"])

    def test_never_commits_without_paths(self):
        git(self.root, "add", "mine.txt")                                    # staged by you
        self.assertEqual(vcs.commit_paths(self.git, [], "nothing"), "")
        self.assertEqual(git(self.root, "log", "--oneline").count("\n"), 0)  # still one commit

    def test_undoes_agent_commits_but_keeps_the_changes(self):
        snapshot = vcs.take_snapshot(self.git)
        (self.root / "agent.py").write_text("y = 2\n")
        git(self.root, "add", "agent.py")
        git(self.root, "commit", "-qm", "agent did this")
        self.assertEqual(vcs.undo_agent_commits(self.git, snapshot), 1)
        self.assertEqual(self.git.head(), snapshot.head)
        self.assertTrue((self.root / "agent.py").exists())

    def test_saves_rejected_work_then_cleans_only_the_run(self):
        snapshot = vcs.take_snapshot(self.git)
        (self.root / "README.md").write_text("changed by the agent\n")
        (self.root / "scratch.py").write_text("z = 3\n")
        changes = vcs.run_changes(self.git, snapshot)
        ref = vcs.save_rejected(self.git, "U001", 3, changes.paths)
        vcs.clean_paths(self.git, changes)
        self.assertEqual(ref, "refs/ralph/rejected/U001/3")
        self.assertIn("scratch.py", git(self.root, "show", "--name-only", "--format=", ref))
        self.assertFalse((self.root / "scratch.py").exists())
        self.assertEqual((self.root / "README.md").read_text(), "project\n")
        self.assertEqual((self.root / "mine.txt").read_text(), "my uncommitted work\n")

    def test_build_artifacts_are_never_the_units(self):
        snapshot = vcs.take_snapshot(self.git)
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "app.cpython-315.pyc").write_bytes(b"\0")
        (self.root / "app.py").write_text("x = 1\n")
        self.assertEqual(vcs.run_changes(self.git, snapshot).paths, ["app.py"])

    def test_paths_ignored_since_an_earlier_attempt_are_skipped(self):
        (self.root / "cache.tmp").write_text("junk\n")
        (self.root / "app.py").write_text("x = 1\n")
        (self.root / ".gitignore").write_text("*.tmp\n")     # added by a later attempt
        sha = vcs.commit_paths(self.git, ["cache.tmp", "app.py", ".gitignore"], "U001")
        self.assertTrue(sha)
        self.assertEqual(self.committed(), {"app.py", ".gitignore"})

    def committed(self) -> set[str]:
        return set(git(self.root, "show", "--name-only", "--format=", "HEAD").split())

    def test_diff_includes_new_files(self):
        (self.root / "fresh.py").write_text("print('hi')\n")
        self.assertIn("+print('hi')", vcs.diff_text(self.git, ["fresh.py"]))


FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, signal, sys, time
prompt = sys.stdin.read()
mode = os.environ.get("FAKE_MODE", "ok")
def emit(event): print(json.dumps(event), flush=True)
if mode == "hang":                 # handle SIGINT before printing anything, so the harness can't beat us to it
    def on_int(*_):
        emit({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": 1})
        sys.exit(130)
    signal.signal(signal.SIGINT, on_int)
emit({"type": "system", "subtype": "init", "session_id": "s", "claude_code_version": "2.1.311"})
if mode == "hang":
    time.sleep(60)
emit({"type": "assistant", "message": {"id": "m", "usage": {"input_tokens": len(prompt)}, "content": []}})
emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2,
      "structured_output": {"unit": "U001", "status": "done", "summary": "ok"}, "argv": sys.argv[1:]})
"""


class SubprocessRunner(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.claude = self.tmp / "claude"
        self.claude.write_text(FAKE_CLAUDE)
        self.claude.chmod(self.claude.stat().st_mode | stat.S_IEXEC)

    def call(self, **overrides) -> AgentCall:
        values = dict(prompt="x" * 200_000, cwd=self.tmp, model="sonnet", log_path=self.tmp / "log" / "stream.jsonl",
                      json_schema={"type": "object"}, system_append="contract", mcp_config=self.tmp / "mcp.json")
        values.update(overrides)
        return AgentCall(**values)

    def test_runs_parses_and_logs(self):
        result = CliRunner(str(self.claude)).run(self.call())    # a prompt larger than a pipe buffer
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.structured, {"unit": "U001", "status": "done", "summary": "ok"})
        self.assertEqual(result.stats.peak_context, 200_000)
        self.assertEqual(len((self.tmp / "log" / "stream.jsonl").read_text().splitlines()), 3)

    def test_passes_the_hermetic_and_bounding_flags(self):
        result = CliRunner(str(self.claude)).run(self.call(max_turns=7, max_budget_usd=1.5))
        argv = " ".join(result.result["argv"])
        for expected in ("--max-turns 7", "--max-budget-usd 1.5", "--strict-mcp-config", "--setting-sources project,local",
                         "--json-schema", "--append-system-prompt contract"):
            self.assertIn(expected, argv)

    def test_a_silent_run_is_stopped_and_its_result_still_read(self):
        import os
        os.environ["FAKE_MODE"] = "hang"
        try:
            started = time.monotonic()
            result = CliRunner(str(self.claude)).run(self.call(silence_seconds=3))
        finally:
            os.environ.pop("FAKE_MODE")
        self.assertEqual(result.stopped_by, "silence")
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(result.subtype, "error_during_execution")   # written on SIGINT, and captured

    def test_no_tools_means_an_empty_tools_flag(self):
        command = build_command(self.call(tools=[]))
        self.assertEqual(command[command.index("--tools") + 1], "")


if __name__ == "__main__":
    unittest.main()
