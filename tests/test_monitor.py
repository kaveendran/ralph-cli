"""Notifications and the browser dashboard."""

import contextlib
import io
import json
import threading
import time
import unittest
import urllib.request

from ralph.config import NotifyConfig
from ralph.model.store import UnitStore
from ralph.model.unit import Status, Unit
from ralph.monitor.dashboard import current_run, serve, state
from ralph.monitor.notify import Notifier
from ralph.monitor.telemetry import IterationRecord, append_record, unit_totals
from ralph.paths import ProjectPaths
from tests.helpers import TempDirTestCase, result_events


class Notifications(TempDirTestCase):
    def test_a_command_gets_the_event_as_json_and_unlisted_events_are_ignored(self):
        out = self.tmp / "events.jsonl"
        notifier = Notifier(NotifyConfig(command=f"cat >> '{out}'; echo >> '{out}'", events=["blocked"]), "shop")
        notifier.send("blocked", "U003 is blocked: needs a key", unit="U003")
        notifier.send("complete", "cr1 is complete")
        (event,) = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
        self.assertEqual((event["event"], event["unit"], event["text"]),
                         ("blocked", "U003", "ralph · shop: U003 is blocked: needs a key"))

    def test_a_failing_notifier_warns_once_and_never_raises(self):
        notifier = Notifier(NotifyConfig(command="exit 3"), "shop")
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(err):
            notifier.send("stopped", "one")
            notifier.send("stopped", "two")
        self.assertEqual(err.getvalue().count("Couldn't send a notification"), 1)

    def test_nothing_is_sent_without_a_destination(self):
        Notifier(NotifyConfig(), "shop").send("blocked", "x")     # no webhook, no command: a no-op


class Totals(unittest.TestCase):
    def test_a_units_cost_includes_its_reviews(self):
        records = [{"cr": "cr1", "unit": "U001", "agent": {"total_cost_usd": 1.0}, "review_cost_usd": 0.25},
                   {"cr": "cr1", "unit": "U001", "agent": {"total_cost_usd": 0.5}},
                   {"cr": "cr2", "unit": "U001", "agent": {"total_cost_usd": 9.0}}]
        self.assertEqual(unit_totals(records, "cr1")["U001"]["cost_usd"], 1.75)


class Dashboard(TempDirTestCase):
    def setUp(self):
        super().setUp()
        self.project = ProjectPaths(self.tmp)
        store = UnitStore(self.project.cr("cr1").units_dir)
        store.save(Unit("U001", "One", "cr1", status=Status.DONE))
        store.save(Unit("U002", "Two", "cr1", status=Status.BLOCKED))
        store.save(Unit("U003", "Three", "cr1"))
        append_record(self.project.iterations_log, IterationRecord("run", 0, "cr1", "U001", 1, "done"))

    def write_stream(self, events: list[dict]) -> None:
        stream = self.project.log_dir / "run" / "00-U002" / "stream.jsonl"
        stream.parent.mkdir(parents=True, exist_ok=True)
        stream.write_text("".join(json.dumps(e) + "\n" for e in events))

    def test_state_counts_units_per_cr_and_includes_the_log(self):
        data = state(self.project)
        self.assertEqual(data["crs"], [{"name": "cr1", "total": 3, "todo": 1, "in_progress": 0, "done": 1,
                                        "blocked": 1}])
        self.assertEqual(data["records"][0]["unit"], "U001")
        self.assertIsNone(data["current"])

    def test_the_running_iteration_shows_until_its_result_arrives(self):
        events = result_events({"unit": "U002"}, peak=80_000)
        self.write_stream(events[:-1])
        run = current_run(self.project.log_dir)
        self.assertEqual((run["run"], run["context_now"], run["tool_calls"]), ("run/00-U002", 80_001, 1))
        self.write_stream(events)
        self.assertIsNone(current_run(self.project.log_dir))

    def test_serves_the_page_and_the_state_on_localhost(self):
        server = serve(self.project, 0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            self.assertEqual(server.server_address[0], "127.0.0.1")
            with urllib.request.urlopen(base + "/api/state", timeout=5) as response:
                self.assertEqual(json.loads(response.read())["crs"][0]["total"], 3)
            with urllib.request.urlopen(base + "/", timeout=5) as response:
                self.assertIn(b"<html", response.read().lower())
            with urllib.request.urlopen(base + "/events", timeout=5) as response:
                first = response.readline()
                self.assertTrue(first.startswith(b"data: {"))
        finally:
            server.shutdown()
            server.server_close()
            time.sleep(0.05)
