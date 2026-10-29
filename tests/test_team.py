"""Manual team sync (P3a): the ledger, interface detection, the impact check,
`ralph sync` and `sync push` between two clones, and proof that the loop
itself never touches the network."""

import contextlib
import io
import json
import os
import socket
import stat
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from ralph.cli import main
from ralph.config import Config, TeamConfig
from ralph.kernel.loop import Kernel, StartOptions
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.paths import ProjectPaths
from ralph.team import interfaces
from ralph.team.impact import ImpactReport, Impact, classify, merge_conflicts, symbol, team_view, touches
from ralph.team.ledger import LedgerEntry, from_others, new_entry, read_local, read_pending, write_entry
from ralph.vcs.git import Git
from tests import helpers
from tests.helpers import (ScriptedRunner, TempDirTestCase, agent_result, done_step, git, make_repo,
                           no_background_git, result_events)

LOOKUP_V1 = "def lookup(query):\n    return [query]\n"
LOOKUP_V2 = "def lookup(query, page):\n    return [query, page]\n"
SKIM = "from serve.lookup import lookup\n\n\ndef skim(q):\n    return lookup(q)[:1]\n"
PLAN = """\
## Unit: Paged lookup (ID: U001)
Make lookup() take a page.

## Unit: Skim (ID: U002)
Return the first lookup() hit.
"""


class Interfaces(unittest.TestCase):
    def test_python_removed_and_reshaped_functions(self):
        before = ("def keep(a, b=1): ...\ndef gone(): ...\ndef _private(x): ...\n"
                  "def grow(a): ...\ndef optional(a): ...\ndef renamed(a): ...\n"
                  "class Store:\n    def get(self, key): ...\n    def _hidden(self): ...\n")
        after = ("def keep(a, b=1): ...\ndef _private(x, y): ...\n"
                 "def grow(a, b): ...\ndef optional(a, b=None): ...\ndef renamed(z): ...\n"
                 "class Store:\n    def get(self, key, *, default=None): ...\n")
        found = {i["name"]: i["breaking"] for i in interfaces.compare("store.py", before, after)}
        self.assertEqual(found, {"gone()": True, "grow()": True, "optional()": False, "renamed()": True,
                                 "Store.get()": False})      # private names are nobody else's business

    def test_new_deleted_and_typescript_files(self):
        self.assertEqual(interfaces.compare("new.py", "", "def f(): ...\n"), [])
        deleted = interfaces.compare("old.py", "def f(): ...\nclass C: ...\n", None)
        self.assertEqual([(i["name"], i["breaking"]) for i in deleted], [("f()", True), ("C", True)])
        ts = interfaces.compare("api.ts", "export function search() {}\nexport const LIMIT = 5\n",
                                "export async function search() {}\n")
        self.assertEqual([i["name"] for i in ts], ["LIMIT"])
        self.assertEqual(interfaces.compare("notes.md", "a", "b"), [])

    def test_the_agents_report_and_detection_are_merged(self):
        merged = interfaces.merge([{"name": "lookup()", "change": "takes a page", "breaking": False},
                                   {"name": "GET /search", "change": "new field", "breaking": False}],
                                  [{"name": "lookup()", "change": "signature changed", "breaking": True},
                                   {"name": "fetch()", "change": "removed", "breaking": True}])
        by_name = {i["name"]: (i["breaking"], i["source"]) for i in merged}
        self.assertEqual(by_name, {"lookup()": (True, "agent+detected"), "GET /search": (False, "agent"),
                                   "fetch()": (True, "detected")})


def entry(unit="U037", email="b@example.com", paths=("serve/lookup.py",), breaking=("lookup()",), at=None,
          pending=False) -> LedgerEntry:
    return LedgerEntry(id=f"20261028T101500Z-cr2-{unit}", unit=unit, cr="cr2", title="Paged lookup", dev="Dev B",
                       email=email, at=at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       files=[{"path": p, "status": "M"} for p in paths],
                       interfaces=[{"name": n, "change": "signature changed", "breaking": True} for n in breaking],
                       commit="def4567890", pending=pending)


class Impacts(TempDirTestCase):
    def test_symbols_and_paths(self):
        self.assertEqual([symbol(n) for n in ("lookup()", "Searcher.find(q)", "GET /search", "api:fetch")],
                         ["lookup", "find", "/search", "fetch"])
        self.assertTrue(touches("serve/a.py", ["serve/*.py"]))
        self.assertTrue(touches("serve/x/a.py", ["serve/"]))
        self.assertFalse(touches("serve2/a.py", ["serve/"]))

    def test_breaking_overlap_info_and_acknowledged(self):
        (self.tmp / "serve").mkdir()
        (self.tmp / "serve" / "skim.py").write_text(SKIM)
        unit = Unit("U038", "Skim", "cr2", key_files=["serve/skim.py", "serve/lookup.py"])
        entries = [entry(), entry("U040", paths=("docs/readme.md",), breaking=())]
        kinds = [(i.kind, i.entry.unit) for i in classify(unit, "Skim results", entries, self.tmp)]
        self.assertEqual(kinds, [("BREAKING", "U037"), ("OVERLAP", "U037"), ("INFO", "U040")])
        breaking = classify(unit, "", entries, self.tmp)[0]
        self.assertIn("lookup(): signature changed — used in serve/skim.py", breaking.line)
        self.assertIn("ack: 20261028T101500Z-cr2-U037", breaking.line)
        unit.acknowledged_impacts = [entries[0].id]
        self.assertEqual([i.entry.unit for i in classify(unit, "", entries, self.tmp)], ["U040"])
        same = Unit("U037", "Paged lookup", "cr2")
        self.assertEqual([i.kind for i in classify(same, "", entries[:1], self.tmp)], ["DUPLICATE"])

    def test_a_plan_entry_that_names_the_interface_counts_as_using_it(self):
        unit = Unit("U039", "Export", "cr2")
        (impact,) = classify(unit, "Call lookup() for each page.", [entry()], self.tmp)
        self.assertEqual(impact.kind, "BREAKING")

    def test_the_digest_is_capped_and_says_how_old_it_is(self):
        long = [Impact("OVERLAP", entry(f"U{n:03d}"), "x" * 200, ["a"]) for n in range(30)]
        now = datetime.now(timezone.utc)
        digest = ImpactReport(long, last_fetch=now - timedelta(hours=3)).digest(now)
        self.assertLessEqual(len(digest), 3_000)
        self.assertIn("As of your last fetch (3 h ago)", digest)
        self.assertRegex(digest, r"…and \d+ more \(see `ralph impact`\)")
        self.assertEqual(ImpactReport([Impact("INFO", entry(), "x")]).digest(), "")

    def test_only_teammates_recent_or_unpulled_entries_count(self):
        old = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        entries = [entry("U1"), entry("U2", email="ME@example.com"), entry("U3", at=old),
                   entry("U4", at=old, pending=True)]
        self.assertEqual([e.unit for e in from_others(entries, "me@example.com", days=7)], ["U1", "U4"])


class Ledger(TempDirTestCase):
    def test_entries_round_trip_and_know_their_commit(self):
        root = make_repo(self.tmp / "repo")
        record = new_entry("U001", "cr1", "Paged lookup", "Dev A", "a@example.com", "abc",
                           [("M", "serve/lookup.py"), ("A", "migrations/0002_page.py")],
                           [{"name": "lookup()", "breaking": True}], "Line one\nTwo\nThree\nFour", ["x"])
        self.assertEqual(record.migrations, ["migrations/0002_page.py"])
        self.assertEqual(record.summary, "Line one\nTwo\nThree")
        path = write_entry(root, record)
        self.assertEqual(read_local(Git(root))[0].commit, "")          # not committed yet
        git(root, "add", "-A")
        git(root, "commit", "-qm", "U001")
        (read,) = read_local(Git(root))
        self.assertEqual((read.id, read.commit, read.paths), (path.stem, git(root, "rev-parse", "HEAD"),
                                                               ["serve/lookup.py", "migrations/0002_page.py"]))
        self.assertNotIn("commit", json.loads(path.read_text()))

    def test_pending_entries_are_read_from_the_shared_branch(self):
        root = make_repo(self.tmp / "repo")
        git(root, "switch", "-qc", "theirs")
        write_entry(root, new_entry("U009", "cr1", "Theirs", "B", "b@example.com", "", [], [], "", []))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "theirs")
        git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(root, "switch", "-q", "work")
        (pending,) = read_pending(Git(root), "refs/remotes/origin/main")
        self.assertEqual((pending.unit, pending.pending), ("U009", True))
        self.assertEqual(read_local(Git(root)), [])

    def test_merge_conflicts_are_found_without_touching_the_tree(self):
        root = make_repo(self.tmp / "repo")
        (root / "a.py").write_text("x = 1\n")
        git(root, "add", "-A")
        git(root, "commit", "-qm", "a")
        git(root, "switch", "-qc", "theirs")
        (root / "a.py").write_text("x = 2\n")
        git(root, "commit", "-qam", "theirs")
        git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
        git(root, "switch", "-q", "work")
        self.assertEqual(merge_conflicts(Git(root), "refs/remotes/origin/main"), [])   # only theirs changed it
        (root / "a.py").write_text("x = 3\n")
        git(root, "commit", "-qam", "mine")
        self.assertEqual(merge_conflicts(Git(root), "refs/remotes/origin/main"), ["a.py"])
        self.assertEqual(git(root, "status", "--porcelain"), "")


# ── Two developers, one shared remote ─────────────────────────────────────────

def ack_step(unit: str, files: dict[str, str], acknowledged: list[str]):
    def step(call):
        for path, text in files.items():
            (call.cwd / path).write_text(text)
        return agent_result(call, result_events({"unit": unit, "status": "done", "summary": f"did {unit}",
                                                 "acknowledged_impacts": acknowledged}))
    return step


class TwoClones(TempDirTestCase):
    """The P3a exit check: two clones of one remote."""

    def setUp(self):
        super().setUp()
        self._cwd, self._path = os.getcwd(), os.environ["PATH"]
        helpers.fake_claude_on_path(self.tmp / "bin")
        self.origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(self.origin)], check=True)
        no_background_git(self.origin)
        self.a = make_repo(self.tmp / "a")
        cr = ProjectPaths(self.a).cr("cr1")
        (cr.dir / "plan").mkdir(parents=True)
        (cr.dir / "plan" / "cr1-implementation-plan.md").write_text(PLAN)
        store = UnitStore(cr.units_dir)
        store.save(Unit("U001", "Paged lookup", "cr1", order=1, key_files=["serve/lookup.py"]))
        store.save(Unit("U002", "Skim", "cr1", order=2, key_files=["serve/skim.py", "serve/lookup.py"]))
        (self.a / "serve").mkdir()
        (self.a / "serve" / "lookup.py").write_text(LOOKUP_V1)
        (self.a / "serve" / "skim.py").write_text(SKIM)
        git(self.a, "add", "-A")
        git(self.a, "commit", "-qm", "plan, units and code")
        git(self.a, "remote", "add", "origin", str(self.origin))
        git(self.a, "push", "-q", "-u", "origin", "work:main")
        self.b = self.tmp / "b"
        subprocess.run(["git", "clone", "-q", "-b", "main", str(self.origin), str(self.b)], check=True)
        no_background_git(self.b)
        git(self.b, "switch", "-qc", "work", "--track", "origin/main")
        git(self.b, "config", "user.email", "b@example.com")
        git(self.b, "config", "user.name", "Dev B")

    def tearDown(self):
        os.chdir(self._cwd)
        os.environ["PATH"] = self._path
        super().tearDown()

    def config(self) -> Config:
        config = Config()
        config.verify.gates = {"unit": "true"}
        config.verify.review = "off"
        config.loop.converge = False
        config.loop.retry_delay_seconds = 0
        return config

    def kernel(self, root, *steps, **options):
        runner = ScriptedRunner(*steps)
        kernel = Kernel(ProjectPaths(root), self.config(), runner,
                        StartOptions(cr="cr1", skip_baseline=True, allow_main=True, **options))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            kernel.run()
        return runner

    def ralph(self, root, *args, answers: str = "") -> tuple[int, str]:
        os.chdir(root)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out), \
                mock.patch.object(sys, "stdin", io.StringIO(answers)):
            code = main(list(args))
        return code, out.getvalue()

    def remote_head(self) -> str:
        return git(self.origin, "rev-parse", "main")

    def test_sync_shows_breaking_and_overlap_and_nothing_is_pushed_without_confirmation(self):
        # Dev A changes lookup()'s signature without reporting it; detection catches it.
        self.kernel(self.a, done_step("U001", {"serve/lookup.py": LOOKUP_V2}), only_units=["U001"])
        (entry_a,) = read_local(Git(self.a))
        self.assertEqual(entry_a.breaking[0]["name"], "lookup()")
        self.assertIn(f".ralph/ledger/{entry_a.id}.json", git(self.a, "show", "--name-only", "--format=", "HEAD"))

        before = self.remote_head()
        code, output = self.ralph(self.a, "sync", "push", answers="n\n")
        self.assertEqual((code, self.remote_head()), (0, before))
        self.assertIn("1 ledger entry", output)
        self.assertIn("Nothing was pushed", output)
        code, output = self.ralph(self.a, "sync", "push", "--yes")
        self.assertEqual((code, self.remote_head()), (0, git(self.a, "rev-parse", "HEAD")), output)

        # Dev B fetches and sees how U001 affects their open U002, without rebasing.
        code, output = self.ralph(self.b, "sync", "--no-rebase")
        self.assertEqual(code, 0, output)
        self.assertIn("1 unit(s) teammates landed that you haven't pulled", output)
        self.assertIn("U001     [DUPLICATE] Test already finished U001", output)
        self.assertIn("U002     [BREAKING] lookup(): signature changed in serve/lookup.py — used in", output)
        self.assertIn("[OVERLAP] serve/lookup.py changed by U001", output)
        self.assertEqual(git(self.b, "rev-parse", "HEAD"), before)     # not rebased

        # B's loop won't build U001 a second time.
        out = io.StringIO()
        runner = ScriptedRunner()
        kernel = Kernel(ProjectPaths(self.b), self.config(), runner,
                        StartOptions(cr="cr1", skip_baseline=True, allow_main=True, only_units=["U001"]))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertEqual(runner.calls, [])
        self.assertIn("Test already finished U001, so building it here would duplicate their work", out.getvalue())

        # Dev B's loop puts it in U002's pack, and the agent acknowledges it.
        runner = self.kernel(self.b, ack_step("U002", {"serve/skim.py": SKIM + "# paged\n"}, [entry_a.id]),
                             only_units=["U002"])
        pack = runner.calls[0].prompt
        self.assertIn("## Teammates' changes that affect this unit", pack)
        self.assertIn("· not pulled yet) — ack: " + entry_a.id, pack)
        self.assertEqual(UnitStore(ProjectPaths(self.b).cr("cr1").units_dir).load()["U002"].acknowledged_impacts,
                         [entry_a.id])
        (entry_b,) = [e for e in read_local(Git(self.b)) if e.unit == "U002"]
        self.assertEqual(entry_b.acknowledged_impacts, [entry_a.id])

        # Rebasing asks first; then B has A's work, and pushing B's work fast-forwards the remote.
        code, output = self.ralph(self.b, "sync", answers="n\n")
        self.assertIn("Not rebased", output)
        code, output = self.ralph(self.b, "sync", "--yes")
        self.assertEqual(code, 0, output)
        self.assertEqual(sorted(e.unit for e in read_local(Git(self.b))), ["U001", "U002"])
        self.assertEqual(self.ralph(self.b, "sync", "push", "--yes")[0], 0)
        self.assertEqual(self.remote_head(), git(self.b, "rev-parse", "HEAD"))

        code, output = self.ralph(self.b, "team")
        self.assertIn("Files more than one of you changed: 0", output)
        code, output = self.ralph(self.a, "impact", "U001")
        self.assertIn("lookup() (breaking)", output)

    def test_push_refuses_when_behind_and_sync_refuses_to_rebase_a_dirty_tree(self):
        self.kernel(self.a, done_step("U001", {"serve/lookup.py": LOOKUP_V2}), only_units=["U001"])
        self.ralph(self.a, "sync", "push", "--yes")
        (self.b / "notes.txt").write_text("b\n")
        git(self.b, "add", "-A")
        git(self.b, "commit", "-qm", "b's own commit")
        self.ralph(self.b, "sync", "--no-rebase")
        code, output = self.ralph(self.b, "sync", "push", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Run 'ralph sync' first", output)
        (self.b / "README.md").write_text("edited\n")
        code, output = self.ralph(self.b, "sync", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("uncommitted changes (README.md)", output)

    def test_the_loop_stops_before_a_unit_whose_files_conflict_with_the_shared_branch(self):
        self.kernel(self.a, done_step("U001", {"serve/lookup.py": LOOKUP_V2}), only_units=["U001"])
        self.ralph(self.a, "sync", "push", "--yes")
        (self.b / "serve" / "lookup.py").write_text("def lookup(query, limit=10):\n    return [query]\n")
        git(self.b, "commit", "-qam", "b changes lookup too")
        self.ralph(self.b, "sync", "--no-rebase")
        runner = ScriptedRunner()           # any call to the agent would fail the test
        kernel = Kernel(ProjectPaths(self.b), self.config(), runner,
                        StartOptions(cr="cr1", skip_baseline=True, allow_main=True, only_units=["U002"]))
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            kernel.run()
        self.assertEqual(runner.calls, [])
        self.assertIn("U002's files conflict with teammates' work on the shared branch: serve/lookup.py", out.getvalue())


class NoNetwork(TempDirTestCase):
    """The loop never contacts the remote: no git transport and no socket."""

    def test_a_whole_iteration_with_a_shared_branch_makes_no_network_calls(self):
        root = make_repo(self.tmp / "project")
        bin_dir = self.tmp / "bin"
        helpers.fake_claude_on_path(bin_dir)
        calls = self.tmp / "transport-calls.log"
        helper = bin_dir / "git-remote-ralphtest"       # git runs this for any fetch/push/ls-remote
        helper.write_text(f"#!/bin/sh\necho \"$@\" >> '{calls}'\nexit 1\n")
        helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
        git(root, "remote", "add", "origin", "ralphtest::nowhere")
        git(root, "update-ref", "refs/remotes/origin/main", "HEAD")      # as if fetched earlier
        git(root, "config", "branch.work.remote", "origin")
        git(root, "config", "branch.work.merge", "refs/heads/main")
        store = UnitStore(ProjectPaths(root).cr("cr1").units_dir)
        store.save(Unit("U001", "One", "cr1", order=1))
        git(root, "add", "-A")
        git(root, "commit", "-qm", "units")
        self.assertNotEqual(subprocess.run(["git", "ls-remote", "origin"], cwd=root, capture_output=True).returncode, 0)
        self.assertTrue(calls.exists())                  # the trap works
        calls.unlink()

        config = Config()
        config.verify.gates = {"unit": "true"}
        config.verify.review = "off"
        config.loop.converge = False
        runner = ScriptedRunner(done_step("U001", {"one.py": "x = 1\n"}))
        kernel = Kernel(ProjectPaths(root), config, runner, StartOptions(cr="cr1", skip_baseline=True))
        path = os.environ["PATH"]
        try:
            with mock.patch.object(socket.socket, "connect", side_effect=AssertionError("network!")), \
                    mock.patch.object(socket, "create_connection", side_effect=AssertionError("network!")), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                kernel.run()
        finally:
            os.environ["PATH"] = path
        self.assertIs(store.load()["U001"].status, Status.DONE)
        self.assertFalse(calls.exists(), calls.read_text() if calls.exists() else "")

    def test_the_kernel_doesnt_import_the_network_module(self):
        probe = "import sys, ralph.kernel.loop, ralph.kernel.modes; print('ralph.team.remote' in sys.modules)"
        result = subprocess.run([sys.executable, "-P", "-c", probe], capture_output=True, text=True,
                                env={**os.environ, "PYTHONPATH": str(helpers.FIXTURES.parent.parent)})
        self.assertEqual(result.stdout.strip(), "False", result.stderr)


if __name__ == "__main__":
    unittest.main()
