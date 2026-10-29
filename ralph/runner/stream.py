"""Read Claude Code's stream-json events as they arrive.

The numbers that matter for a loop harness:
- peak context: the largest prompt the main agent sent (input + cache-read +
  cache-creation tokens). It drives per-turn latency, so it's what to watch
  when iterations get slow. Usage is counted once per message id, because a
  message's usage repeats on each of its content blocks. Subagents run in
  their own context window, so their messages don't count toward the peak;
- compactions and API retries;
- tool calls, and the longest run of the exact same call back to back (a
  sign of looping; rerunning a test between edits is normal and not counted);
- the final `result` event: subtype, turns, duration, cost, structured output.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field

SUBAGENT_TOOLS = {"Agent", "Task"}


@dataclass
class StreamStats:
    peak_context: int = 0
    context_now: int = 0
    compactions: int = 0
    api_retries: int = 0
    tool_calls: Counter = field(default_factory=Counter)
    repeated_call_max: int = 0           # longest run of one identical tool call, back to back
    last_text: str = ""                  # the main agent's latest text
    usage_limited: bool = False
    init: dict | None = None
    result: dict | None = None
    events: int = 0
    _seen_messages: set = field(default_factory=set)
    _last_call: str = ""
    _repeat_run: int = 0

    @property
    def subagent_calls(self) -> int:
        return sum(self.tool_calls[name] for name in SUBAGENT_TOOLS)

    def feed_line(self, line: str) -> dict | None:
        """Parse one line of output; returns the event, or None if it isn't JSON."""
        line = line.strip()
        if not line.startswith("{"):
            return None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        self.feed(event)
        return event

    def feed(self, event: dict) -> None:
        self.events += 1
        kind = event.get("type")
        if kind == "result":
            self.result = event
        elif kind == "system":
            self._system(event)
        elif kind == "rate_limit_event":
            info = event.get("rate_limit_info") or event
            if str(info.get("status", "")).lower() == "rejected":
                self.usage_limited = True
        elif kind == "assistant" and not event.get("parent_tool_use_id"):
            self._assistant(event.get("message") or {})

    def _system(self, event: dict) -> None:
        subtype = event.get("subtype")
        if subtype == "init":
            self.init = event
        elif subtype == "compact_boundary":
            self.compactions += 1
        elif subtype == "api_retry":
            self.api_retries += 1

    def _assistant(self, message: dict) -> None:
        for block in message.get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                name = block.get("name", "?")
                self.tool_calls[name] += 1
                fingerprint = hashlib.sha1(
                    (name + json.dumps(block.get("input"), sort_keys=True, default=str)).encode()).hexdigest()
                self._repeat_run = self._repeat_run + 1 if fingerprint == self._last_call else 1
                self._last_call = fingerprint
                self.repeated_call_max = max(self.repeated_call_max, self._repeat_run)
            elif block.get("type") == "text" and block.get("text"):
                self.last_text = block["text"]
        message_id = message.get("id")
        if message_id in self._seen_messages:
            return
        self._seen_messages.add(message_id)
        usage = message.get("usage") or {}
        self.context_now = sum(usage.get(k) or 0 for k in
                               ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
        self.peak_context = max(self.peak_context, self.context_now)

    def summary(self) -> dict:
        """The numbers recorded in log/iterations.jsonl."""
        result = self.result or {}
        return {
            "subtype": result.get("subtype"),
            "is_error": result.get("is_error"),
            "num_turns": result.get("num_turns"),
            "duration_ms": result.get("duration_ms"),
            "duration_api_ms": result.get("duration_api_ms"),
            "total_cost_usd": result.get("total_cost_usd"),
            "session_id": result.get("session_id") or (self.init or {}).get("session_id"),
            "claude_code_version": (self.init or {}).get("claude_code_version"),
            "peak_context": self.peak_context,
            "compactions": self.compactions,
            "api_retries": self.api_retries,
            "permission_denials": len(result.get("permission_denials") or []),
            "subagent_calls": self.subagent_calls,
            "repeated_call_max": self.repeated_call_max,
            "tool_calls": dict(sorted(self.tool_calls.items())),
        }


def describe_tool_use(event: dict) -> list[str]:
    """Short lines for the live view, one per tool call in an assistant event."""
    if event.get("type") != "assistant" or event.get("parent_tool_use_id"):
        return []
    lines = []
    for block in (event.get("message") or {}).get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name, data = block.get("name", "?"), block.get("input") or {}
        if name == "StructuredOutput":
            continue  # the final report, shown in the iteration summary instead
        detail = data.get("file_path") or data.get("command") or data.get("description") or data.get("pattern") or ""
        detail = " ".join(str(detail).split())
        lines.append(f"{name:<7} {detail[:110]}")
    return lines
