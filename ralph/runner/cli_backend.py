"""Run `claude -p` as a subprocess and watch its stream-json output.

The child runs in its own process group, so the harness can stop the whole
tree (claude, its tools, any Docker compose it started) without leaving
orphans. Stopping is gentle first: SIGINT lets Claude Code write its result
event and flush telemetry, and the harness keeps reading output meanwhile so
that result is recorded. SIGTERM and SIGKILL follow only if it doesn't exit.
A Ctrl-C in the terminal is handled the same way, then re-raised.
"""

import json
import os
import selectors
import signal
import subprocess
import threading
import time

from ralph.runner.base import AgentCall, AgentResult, EventCallback
from ralph.runner.stream import StreamStats

STOP_SEQUENCE = ((signal.SIGINT, 15.0), (signal.SIGTERM, 10.0), (signal.SIGKILL, 5.0))


def build_command(call: AgentCall, claude: str = "claude") -> list[str]:
    command = [claude, "-p", "--output-format", "stream-json", "--verbose",
               "--model", call.model, "--max-turns", str(call.max_turns),
               "--max-budget-usd", f"{call.max_budget_usd:g}"]
    if call.skip_permissions:
        command.append("--dangerously-skip-permissions")
    if call.tools is not None:
        command += ["--tools", ",".join(call.tools)]
    if call.json_schema:
        command += ["--json-schema", json.dumps(call.json_schema, separators=(",", ":"))]
    if call.system_append:
        command += ["--append-system-prompt", call.system_append]
    if call.agents_file:
        command += ["--agents", str(call.agents_file)]
    if call.settings_file:
        command += ["--settings", str(call.settings_file)]
    if call.hermetic:
        if call.mcp_config:
            command += ["--strict-mcp-config", "--mcp-config", str(call.mcp_config)]
        command += ["--setting-sources", call.setting_sources]
    return command


def describe_command(command: list[str]) -> str:
    """The command for display; long values (the contract, the schema) are shortened."""
    shown, skip = [], None
    for part in command:
        if skip:
            shown.append(f"<{skip}>")
            skip = None
            continue
        shown.append(part)
        skip = {"--append-system-prompt": "contract", "--json-schema": "schema"}.get(part)
    return " ".join(shown)


class _Session:
    """One running `claude` process: its output, its log and its stats."""

    def __init__(self, process: subprocess.Popen, log, stats: StreamStats, on_event: EventCallback | None):
        self.process, self.log, self.stats, self.on_event = process, log, stats, on_event
        self.selector = selectors.DefaultSelector()
        self.selector.register(process.stdout, selectors.EVENT_READ)
        self.finished = False

    def read(self, timeout: float) -> str | None:
        """Handle the next output line, if one arrives within `timeout`.

        Returns a stop reason from the event callback, else None.
        """
        if self.finished or not self.selector.select(timeout=timeout):
            return None
        line = self.process.stdout.readline()
        if line == "":
            self.finished = True
            return None
        self.log.write(line)
        self.log.flush()
        event = self.stats.feed_line(line)
        if event is not None and self.on_event:
            return self.on_event(event, self.stats)
        return None

    def stop(self) -> None:
        """Stop the process group, reading its last output while it shuts down."""
        for sig, grace in STOP_SEQUENCE:
            if self.finished and self.process.poll() is not None:
                return
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline and not self.finished:
                self.read(timeout=0.5)
            if self.finished:
                return


class CliRunner:
    def __init__(self, claude: str = "claude"):
        self.claude = claude

    def run(self, call: AgentCall, on_event: EventCallback | None = None) -> AgentResult:
        command = build_command(call, self.claude)
        call.log_path.parent.mkdir(parents=True, exist_ok=True)
        stats = StreamStats()
        process = subprocess.Popen(command, cwd=call.cwd, env={**os.environ, **call.env}, text=True,
                                   bufsize=1, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, start_new_session=True)
        # Send the prompt from a thread: a prompt larger than the pipe buffer
        # would otherwise block before we start reading the output.
        threading.Thread(target=_send_prompt, args=(process, call.prompt), daemon=True).start()

        with open(call.log_path, "w", encoding="utf-8") as log:
            session = _Session(process, log, stats, on_event)
            try:
                stopped_by = self._watch(session, call)
            except KeyboardInterrupt:
                session.stop()
                raise
            if stopped_by:
                session.stop()
        return AgentResult(exit_code=process.wait(), stats=stats, stopped_by=stopped_by)

    @staticmethod
    def _watch(session: _Session, call: AgentCall) -> str:
        """Follow the run until it ends; return why we stopped it, if we did."""
        started = last_output = time.monotonic()
        events_seen = 0
        while not session.finished:
            reason = session.read(timeout=1.0)
            if reason:
                return reason
            if session.stats.events != events_seen:
                events_seen, last_output = session.stats.events, time.monotonic()
            if call.cancel is not None and call.cancel.is_set():
                return "interrupted"
            now = time.monotonic()
            if now - started > call.timeout_seconds:
                return "timeout"
            if now - last_output > call.silence_seconds:
                return "silence"
        return ""


def _send_prompt(process: subprocess.Popen, prompt: str) -> None:
    try:
        process.stdin.write(prompt)
        process.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # the process exited early; its output says why
