"""What a runner is asked to do, and what it reports back."""

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ralph.runner.stream import StreamStats


@dataclass
class AgentCall:
    prompt: str
    cwd: Path
    model: str
    log_path: Path                       # the raw stream-json is written here
    max_turns: int = 200
    max_budget_usd: float = 30.0
    timeout_seconds: int = 90 * 60
    silence_seconds: int = 10 * 60       # no output for this long: the run is stuck
    system_append: str = ""              # added to Claude Code's own system prompt
    json_schema: dict | None = None      # structured result
    tools: list[str] | None = None       # None: the default tools; []: no tools at all
    skip_permissions: bool = False
    permission_mode: str = ""            # e.g. "dontAsk" or "acceptEdits", when not skipping permissions
    allowed_tools: list[str] = field(default_factory=list)      # allow rules, e.g. "Bash(pytest *)"
    settings_file: Path | None = None    # per-run settings: guardrail hooks
    agents_file: Path | None = None      # subagent definitions (the explorer)
    hermetic: bool = True
    mcp_config: Path | None = None
    setting_sources: str = "project,local"
    env: dict[str, str] = field(default_factory=dict)
    # Set from another thread to stop the run (parallel workflow calls, which a
    # terminal Ctrl-C can't reach: each runs in its own process group).
    cancel: threading.Event | None = None


@dataclass
class AgentResult:
    exit_code: int
    stats: StreamStats
    stopped_by: str = ""                 # "timeout", "silence", "looping" or "interrupted"

    @property
    def result(self) -> dict:
        return self.stats.result or {}

    @property
    def subtype(self) -> str:
        return self.result.get("subtype", "")

    @property
    def structured(self) -> dict | None:
        value = self.result.get("structured_output")
        return value if isinstance(value, dict) else None

    @property
    def capped(self) -> bool:
        """Stopped by a turn, budget or time cap: an expected outcome, not a failure."""
        return self.subtype in ("error_max_turns", "error_max_budget_usd") or self.stopped_by == "timeout"

    @property
    def usage_limited(self) -> bool:
        return self.stats.usage_limited

    @property
    def failed(self) -> bool:
        """The run itself broke (API error, crash, no result), as opposed to the unit failing."""
        if self.capped or self.stopped_by in ("looping", "silence"):
            return False
        return self.stats.result is None or (self.exit_code != 0 and self.subtype != "success")


EventCallback = Callable[[dict, StreamStats], str | None]
"""Called for every stream event. Return a reason string to stop the run."""


class Runner(Protocol):
    def run(self, call: AgentCall, on_event: EventCallback | None = None) -> AgentResult: ...
