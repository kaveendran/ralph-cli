"""`ralph watch --serve`: the live view in a browser.

A small local web server (stdlib only, bound to 127.0.0.1) serves one page
and pushes the loop's state to it with server-sent events every two seconds:
unit progress per CR, the iteration log, and the running iteration's context,
tool calls and compactions.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ralph.model.store import UnitStore
from ralph.model.unit import Status
from ralph.monitor.telemetry import read_records
from ralph.paths import ProjectPaths
from ralph.prompts import TEMPLATES_DIR
from ralph.runner.stream import StreamStats, describe_tool_use

PUSH_SECONDS = 2.0


def current_run(log_dir: Path, recent: int = 12) -> dict | None:
    streams = sorted(log_dir.glob("*/*/stream.jsonl"), key=lambda p: p.stat().st_mtime)
    if not streams or time.time() - streams[-1].stat().st_mtime > 15 * 60:
        return None
    stats, tools = StreamStats(), []
    for line in streams[-1].read_text(encoding="utf-8", errors="replace").splitlines():
        event = stats.feed_line(line)
        if event:
            tools.extend(describe_tool_use(event))
    if stats.result is not None:
        return None    # that iteration has finished
    return {"run": f"{streams[-1].parent.parent.name}/{streams[-1].parent.name}", "context_now": stats.context_now,
            "peak_context": stats.peak_context, "tool_calls": sum(stats.tool_calls.values()),
            "compactions": stats.compactions, "recent_tools": tools[-recent:]}


def state(project: ProjectPaths) -> dict:
    crs = []
    for name in project.cr_names():
        store = UnitStore(project.cr(name).units_dir)
        if store.exists():
            units = store.load().values()
            crs.append({"name": name, "total": len(units),
                        **{s.value: sum(1 for u in units if u.status is s) for s in Status}})
    return {"root": str(project.root), "crs": crs, "records": read_records(project.iterations_log)[-50:],
            "current": current_run(project.log_dir)}


def make_handler(project: ProjectPaths):
    page = (TEMPLATES_DIR / "dashboard.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/":
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page)
            elif self.path == "/api/state":
                body = json.dumps(state(project)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)
            elif self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                try:
                    while True:
                        self.wfile.write(f"data: {json.dumps(state(project))}\n\n".encode())
                        self.wfile.flush()
                        time.sleep(PUSH_SECONDS)
                except (BrokenPipeError, ConnectionResetError):
                    return
            else:
                self.send_error(404)

    return Handler


def serve(project: ProjectPaths, port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(project))
    server.daemon_threads = True
    return server
