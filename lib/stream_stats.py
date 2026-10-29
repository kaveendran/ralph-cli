"""Summarise one iteration's `claude -p --output-format stream-json` log.

Usage:
    python3 -I stream_stats.py <log_file>            # one JSON object
    python3 -I stream_stats.py <log_file> <field>    # just that field

Peak context is the largest prompt the main agent sent in one request
(input + cache-read + cache-creation tokens). That number drives per-turn
latency, so it's the one to watch when the loop gets slow. Usage is counted
once per message id, because a message's usage repeats on every content block.
Subagent messages (those with a parent_tool_use_id) are excluded from the
peak, since they run in their own context window.
"""

import json
import sys

SUBAGENT_TOOLS = {"Agent", "Task"}


def summarise(path):
    peak_context = 0
    seen_messages = set()
    compactions = 0
    api_retries = 0
    tool_calls = {}
    result = {}

    with open(path, encoding="utf-8", errors="replace") as log:
        for line in log:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            kind = event.get("type")
            if kind == "result":
                result = event
            elif kind == "system":
                subtype = event.get("subtype")
                if subtype == "compact_boundary":
                    compactions += 1
                elif subtype == "api_retry":
                    api_retries += 1
            elif kind == "assistant" and not event.get("parent_tool_use_id"):
                message = event.get("message") or {}
                for block in message.get("content") or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        name = block.get("name", "?")
                        tool_calls[name] = tool_calls.get(name, 0) + 1
                message_id = message.get("id")
                if message_id in seen_messages:
                    continue
                seen_messages.add(message_id)
                usage = message.get("usage") or {}
                context = sum(
                    usage.get(key) or 0
                    for key in (
                        "input_tokens",
                        "cache_read_input_tokens",
                        "cache_creation_input_tokens",
                    )
                )
                peak_context = max(peak_context, context)

    return {
        "subtype": result.get("subtype"),
        "is_error": result.get("is_error"),
        "num_turns": result.get("num_turns"),
        "duration_ms": result.get("duration_ms"),
        "duration_api_ms": result.get("duration_api_ms"),
        "total_cost_usd": result.get("total_cost_usd"),
        "session_id": result.get("session_id"),
        "peak_context": peak_context,
        "compactions": compactions,
        "api_retries": api_retries,
        "permission_denials": len(result.get("permission_denials") or []),
        "subagent_calls": sum(tool_calls.get(name, 0) for name in SUBAGENT_TOOLS),
        "tool_calls": dict(sorted(tool_calls.items())),
    }


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        stats = summarise(argv[1])
    except OSError as error:
        print(f"stream_stats: {error}", file=sys.stderr)
        return 1
    if len(argv) == 3:
        value = stats.get(argv[2])
        print("" if value is None else value)
    else:
        print(json.dumps(stats, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
