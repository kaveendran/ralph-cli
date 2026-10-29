"""`ralph watch` — follow a running loop from another terminal, or in a browser."""

import time
from pathlib import Path

from ralph import ui
from ralph.commands.common import load_project
from ralph.monitor.dashboard import serve
from ralph.monitor.telemetry import read_records
from ralph.runner.stream import StreamStats, describe_tool_use


def register(subparsers) -> None:
    p = subparsers.add_parser("watch", help="follow the running loop: tool calls, context and cost as they happen")
    p.add_argument("--no-follow", action="store_true", help="show the recent iterations and exit")
    p.add_argument("--serve", action="store_true", help="serve a live dashboard on http://127.0.0.1:<port>")
    p.add_argument("--port", type=int, default=8765, help="dashboard port (default 8765)")
    p.set_defaults(handler=run)


def run_dashboard(port: int) -> int:
    project, _ = load_project()
    server = serve(project, port)
    ui.header("watch — dashboard", f"http://127.0.0.1:{server.server_port} (local only; Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _record_line(record: dict) -> str:
    agent = record.get("agent") or {}
    cost = (agent.get("total_cost_usd") or 0) + (record.get("review_cost_usd") or 0)
    return (f"  {record.get('run_id', '')} #{record.get('iteration', 0):<2} {record.get('unit', '?'):<10} "
            f"{record.get('outcome', '?'):<11} {agent.get('num_turns') or 0:>4} turns "
            f"{(agent.get('duration_ms') or 0) / 60000:>5.1f} min ${cost:>6.2f} "
            f"peak {agent.get('peak_context') or 0:>9,}"
            + (f" · {agent['permission_denials']} denied" if agent.get("permission_denials") else ""))


def _latest_stream(log_dir: Path) -> Path | None:
    streams = sorted(log_dir.glob("*/*/stream.jsonl"), key=lambda p: p.stat().st_mtime)
    return streams[-1] if streams else None


def run(args) -> int:
    if args.serve:
        return run_dashboard(args.port)
    project, _ = load_project()
    records = read_records(project.iterations_log)
    ui.header("watch", str(project.log_dir))
    for record in records[-5:]:
        ui.info(_record_line(record))
    if args.no_follow:
        return 0

    ui.info("\n  Following… (Ctrl-C to stop)")
    seen_records, stream, position, stats, last_status = len(records), None, 0, StreamStats(), 0.0
    try:
        while True:
            records = read_records(project.iterations_log)
            for record in records[seen_records:]:
                ui.info(_record_line(record))
            seen_records = len(records)

            latest = _latest_stream(project.log_dir)
            if latest and latest != stream:
                stream, position, stats = latest, 0, StreamStats()
                ui.info(f"\n  → {stream.parent.name}")
            if stream and stream.exists():
                with open(stream, encoding="utf-8") as handle:
                    handle.seek(position)
                    for line in handle:
                        event = stats.feed_line(line)
                        if event:
                            for described in describe_tool_use(event):
                                ui.info(f"    {described}")
                    position = handle.tell()
            if stream and time.monotonic() - last_status > 30:
                last_status = time.monotonic()
                ui.info(f"    · context {stats.context_now:,} (peak {stats.peak_context:,}) · "
                        f"{sum(stats.tool_calls.values())} tool calls · {stats.compactions} compactions")
            time.sleep(1)
    except KeyboardInterrupt:
        return 0
