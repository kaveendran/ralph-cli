"""Shared test helpers: temporary git repos, sample plans, and a scripted runner."""

import json
import os
import stat
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from ralph.runner.base import AgentCall, AgentResult
from ralph.runner.stream import StreamStats

FIXTURES = Path(__file__).parent / "fixtures"

# A plan in the shape real plans take: milestones, bold IDs, notes in
# parentheses, cross-CR dependencies, an "Enables" line, an order table.
SAMPLE_PLAN = """\
# Implementation Plan: CR2

## 4. Implementation Units

## Unit: Search endpoint (ID: U001)

**Status:** 🔵 Not Started
**Milestone:** S1
**Priority:** P0-Critical
**Type:** api
**Size Estimate:** Large
**Spend:** none¹

### Goal
Serve `POST /search`. See §4.1 and CR1 §4.2; the plan's own §4b doesn't count.

### Deliverables (files)
- `backend/search.py` — **write**: the endpoint.
- `backend/tests/test_search.py` — **write**: its tests.

### Dependencies
- **Depends On:** [CR1 U017 (`_candidates`, fusion), U018 (`fetch`,
  the raster reference), **U025** (envelope)]
- **Enables:** [U002]

### Acceptance Criteria
- [ ] WHEN a query arrives, THE SYSTEM SHALL return rows (AC-001).

## Unit: Keyword filters (ID: U002)

**Priority:** P1-High
**Type:** api
**Size Estimate:** Medium

### Dependencies
- **Depends On:** [U001, CR1 U004 (`variants`)]
- **Enables:** [U003]

## Unit: CLI surface (ID: U003)

**Priority:** P1-High
**Type:** component
**Size Estimate:** Small
**Spend:** paid

### Dependencies
- **Depends On:** [U001, U002]

## 10. Implementation Order

| # | Unit | State |
|---|---|---|
| 1 | U001 | shipped |
| 2 | **U002** | next |
| 3 | U003 | later |
"""

SAMPLE_SPEC = """\
# CR2 Specification

## 4. The surface contract

### 4.1 Request
The request has a query.

### 4.2 Response
Rows come back.

## 9. Top-level acceptance criteria
- AC-001 WHEN a query arrives, THE SYSTEM SHALL return rows.
"""


class TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()


def git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout.strip()


def no_background_git(root: Path) -> None:
    """Recent git starts maintenance in the background after commits and pushes;
    in a test repo it can still be writing packs while the test deletes the folder."""
    for key, value in (("gc.auto", "0"), ("maintenance.auto", "false"), ("receive.autogc", "false")):
        git(root, "config", key, value)


def make_repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "work")
    no_background_git(root)
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "user.name", "Test")
    (root / "README.md").write_text("project\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    return root


def fake_claude_on_path(bin_dir: Path, version: str = "2.1.311") -> None:
    """A `claude` that only answers --version and --help, for preflight checks."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(f"#!/bin/sh\ncase \"$1\" in --version) echo '{version} (Claude Code)';; "
                      f"*) echo '--print --output-format --verbose --model --max-budget-usd "
                      f"--dangerously-skip-permissions --tools --json-schema --append-system-prompt --agents "
                      f"--settings --strict-mcp-config --mcp-config --setting-sources';; esac\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


def result_events(structured: dict | None = None, subtype: str = "success", turns: int = 5,
                  peak: int = 50_000, cost: float = 0.5) -> list[dict]:
    return [
        {"type": "system", "subtype": "init", "session_id": "s", "claude_code_version": "2.1.311"},
        {"type": "assistant", "message": {"id": "m1", "usage": {"input_tokens": 1, "cache_read_input_tokens": peak},
                                          "content": [{"type": "tool_use", "name": "Edit", "input": {"file_path": "x"}}]}},
        {"type": "result", "subtype": subtype, "is_error": subtype != "success", "num_turns": turns,
         "duration_ms": 60_000, "duration_api_ms": 50_000, "total_cost_usd": cost,
         **({"structured_output": structured} if structured is not None else {})},
    ]


def agent_result(call: AgentCall, events: list[dict], exit_code: int = 0, stopped_by: str = "") -> AgentResult:
    stats = StreamStats()
    call.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(call.log_path, "w") as log:
        for event in events:
            log.write(json.dumps(event) + "\n")
            stats.feed(event)
    return AgentResult(exit_code=exit_code, stats=stats, stopped_by=stopped_by)


class ScriptedRunner:
    """Plays the agent: each step edits files in the project and returns a result."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[AgentCall] = []

    def run(self, call: AgentCall, on_event=None) -> AgentResult:
        self.calls.append(call)
        if not self.steps:
            raise AssertionError("the loop asked for more runs than the test scripted")
        return self.steps.pop(0)(call)


class PromptRunner:
    """Answers each call by the first rule whose marker appears in its prompt.

    For the parallel workflow calls, which arrive in any order. An answer is a
    string (the text result), a dict (a structured result), or a list of those
    (one per matching call, in turn). It records how many calls overlapped.
    """

    def __init__(self, *rules, delay: float = 0.05):
        self.rules = [(marker, list(answer) if isinstance(answer, list) else answer) for marker, answer in rules]
        self.delay = delay
        self.calls: list[AgentCall] = []
        self.active = self.most_at_once = 0
        self._lock = threading.Lock()

    def run(self, call: AgentCall, on_event=None) -> AgentResult:
        with self._lock:
            self.calls.append(call)
            self.active += 1
            self.most_at_once = max(self.most_at_once, self.active)
            answer = next((a for marker, a in self.rules if marker in call.prompt), None)
            if isinstance(answer, list):
                answer = answer.pop(0) if len(answer) > 1 else answer[0]
        try:
            time.sleep(self.delay)        # let parallel calls overlap
            if answer is None:
                raise AssertionError(f"no rule matches the prompt: {call.prompt[:80]!r}")
            if isinstance(answer, dict):
                event = {"type": "result", "subtype": "success", "total_cost_usd": 0.01, "structured_output": answer}
            else:
                event = {"type": "result", "subtype": "success", "total_cost_usd": 0.01, "result": answer}
            return agent_result(call, [{"type": "system", "subtype": "init"}, event])
        finally:
            with self._lock:
                self.active -= 1

    def prompts(self, marker: str) -> list[str]:
        return [c.prompt for c in self.calls if marker in c.prompt]


def done_step(unit: str, files: dict[str, str], handoff: str = "", issues=(), commit: bool = False, learnings=()):
    """A step that writes `files` and reports the unit done."""
    def step(call: AgentCall) -> AgentResult:
        for path, text in files.items():
            target = call.cwd / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        if commit:   # an agent ignoring the contract
            git(call.cwd, "add", "-A")
            git(call.cwd, "commit", "-qm", "agent commit")
        report = {"unit": unit, "status": "done", "summary": f"did {unit}", "handoff": handoff,
                  "discovered_issues": list(issues), "learnings": list(learnings)}
        return agent_result(call, result_events(report))
    return step


def report_step(unit: str, status: str, files: dict[str, str] | None = None, blocker: str = ""):
    def step(call: AgentCall) -> AgentResult:
        for path, text in (files or {}).items():
            (call.cwd / path).parent.mkdir(parents=True, exist_ok=True)
            (call.cwd / path).write_text(text)
        report = {"unit": unit, "status": status, "summary": status, "blocker": blocker}
        return agent_result(call, result_events(report))
    return step


def failed_step(call: AgentCall) -> AgentResult:
    return agent_result(call, [{"type": "system", "subtype": "init"}], exit_code=1)


def capped_step(call: AgentCall) -> AgentResult:
    return agent_result(call, result_events(None, subtype="error_max_turns"), exit_code=1)


def usage_limit_step(call: AgentCall) -> AgentResult:
    return agent_result(call, [{"type": "rate_limit_event", "rate_limit_info": {"status": "rejected"}},
                               *result_events(None, subtype="success")], exit_code=1)
