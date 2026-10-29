"""Live warning signs while an agent runs.

Each alert is raised once per run. Most only warn; repeating the exact same
tool call many times means the agent is looping, so that stops the run.
Thresholds follow docs/meta-harness-plan.md §10.2.
"""

from dataclasses import dataclass, field

from ralph.runner.stream import StreamStats


@dataclass
class Thresholds:
    context_warn: int = 160_000          # 80% of the 200k compaction window
    retries_warn: int = 5
    repeat_warn: int = 4
    repeat_stop: int = 8


@dataclass
class SignalMonitor:
    thresholds: Thresholds = field(default_factory=Thresholds)
    raised: set = field(default_factory=set)

    def check(self, stats: StreamStats) -> tuple[list[str], str | None]:
        """(new alerts, a reason to stop the run or None)."""
        alerts = []
        t = self.thresholds

        def once(key: str, message: str) -> None:
            if key not in self.raised:
                self.raised.add(key)
                alerts.append(message)

        if stats.context_now > t.context_warn:
            once("context", f"context is at {stats.context_now:,} tokens; this unit may be too big for one session")
        if stats.compactions:
            once(f"compaction-{stats.compactions}", f"context was compacted ({stats.compactions}×); "
                 "two compactions on one unit mean it should be split")
        if stats.api_retries >= t.retries_warn:
            once("retries", f"{stats.api_retries} API retries so far")
        if stats.usage_limited:
            once("usage", "the usage limit was reached")
        if stats.repeated_call_max >= t.repeat_warn:
            once("repeat", f"the same tool call was made {stats.repeated_call_max} times in a row; "
                 "the agent may be looping")
        stop = "looping" if stats.repeated_call_max >= t.repeat_stop else None
        return alerts, stop
